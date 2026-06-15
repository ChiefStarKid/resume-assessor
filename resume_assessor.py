"""
Resume Assessor — LLM-powered TC screening pipeline.

Evaluates a resume against one or more job descriptions through five sequential
stages, each built on a discrete system prompt. Stages chain output forward;
early exit on KILL at the reject gate.

Pipeline stages (sequential, early-exit):
    0. ats_simulation   — 15-second ATS triage pass
    1. reject_gate      — adversarial; KILL stops everything for that JD
    2. signal_quality   — oversell/undersell calibration
    3. jd_fit           — floor check; receives signal_quality + gate concerns
    4. candidate_read   — ceiling signals + interview probes
    5. tc_brief_stub    — draft HM brief (TC edits post-interview)

Requires Claude Code CLI (`claude`) installed and authenticated.
Uses `claude -p` (session auth) — no separate ANTHROPIC_API_KEY needed.

Usage:
    python resume_assessor.py --resume path/to/resume.md
    python resume_assessor.py --resume path/to/resume.md --jd-dir ./jds/
    python resume_assessor.py --resume path/to/resume.md --jd path/to/single.md
    python resume_assessor.py --resume path/to/resume.md --models claude-opus-4-8,claude-sonnet-4-6

Models:
    claude-opus-4-8              (most thorough)
    claude-sonnet-4-6            (balanced — default)
    claude-haiku-4-5-20251001    (fastest)

Paths (all have defaults, all overridable via flags):
    --resume      Resume markdown file (required)
    --jd-dir      Directory of JD markdown files (default: ./jds/)
    --jd          Single JD file — skips the JD directory entirely
    --output-dir  Where reports are saved (default: ./reports/)
    --prompts     Prompt file (default: assessor_prompt.md next to this script)
"""

import argparse
import os
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

MODEL_SLUGS = {
    "claude-opus-4-8": "opus",
    "claude-sonnet-4-6": "sonnet",
    "claude-haiku-4-5-20251001": "haiku",
}

AVAILABLE_MODELS = list(MODEL_SLUGS.keys())

DEFAULT_PROMPTS = Path(__file__).parent / "assessor_prompt.md"
DEFAULT_JD_DIR = Path("jds")
DEFAULT_OUTPUT_DIR = Path("reports")


def call_claude(prompt: str, model: str, timeout: int = 180) -> str:
    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
    cmd = ["claude", "-p", "--output-format", "text", "--model", model]
    result = subprocess.run(cmd, input=prompt.encode("utf-8"), capture_output=True, env=env, timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError(f"claude -p failed: {result.stderr.decode('utf-8', errors='replace').strip()}")
    return result.stdout.decode("utf-8").strip()


def load_prompt_section(prompt_file: Path, section_name: str) -> str:
    content = prompt_file.read_text(encoding="utf-8")
    pattern = rf"## \[{re.escape(section_name)}\]\n(.*?)(?=\n## \[|\Z)"
    match = re.search(pattern, content, re.DOTALL)
    if not match:
        print(f"Warning: section [{section_name}] not found in {prompt_file}", file=sys.stderr)
        return ""
    return match.group(1).strip()


def find_latest_resume(directory: Path) -> Path:
    candidates = sorted(
        directory.glob("*.md"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        print(f"Error: no .md files found in {directory}", file=sys.stderr)
        sys.exit(1)
    return candidates[0]


def derive_label(path: Path) -> str:
    """Derive a human-readable label from a file path for report output."""
    return path.stem.replace("_", " ").replace("-", " ").title()


def load_jds(jd_dir: Path) -> list:
    jd_files = sorted(jd_dir.glob("*.md"))
    if not jd_files:
        print(f"No JD files found in {jd_dir}", file=sys.stderr)
        print("Add one or more job description markdown files to that directory.", file=sys.stderr)
        sys.exit(1)

    jds = []
    for f in jd_files:
        content = f.read_text(encoding="utf-8")
        heading_match = re.match(r"^# (.+)", content)
        label = heading_match.group(1).strip() if heading_match else derive_label(f)
        jds.append({"path": f, "label": label, "content": content})
    return jds


def extract_verdict(text: str, field: str = "Verdict") -> str:
    match = re.search(rf"\*\*{re.escape(field)}:\*\*\s*(.+)", text)
    return match.group(1).strip() if match else "?"


def extract_kill_oneliner(text: str) -> str:
    match = re.search(r"\*\*If KILL[^:*]*:\*\*\s*(.+)", text)
    return match.group(1).strip() if match else "(no one-liner produced)"


def extract_ats_verdict(text: str) -> str:
    match = re.search(r"\*\*Verdict:\*\*\s*(.+)", text)
    if not match:
        return "—"
    v = match.group(1).strip()
    if "Likely Filtered" in v:
        return "Likely Filtered"
    if "At Risk" in v:
        return "At Risk"
    if "Pass" in v:
        return "Pass"
    return v


def shared_context(resume_text: str, jd_content: str) -> str:
    return f"Candidate Resume:\n\n{resume_text}\n\n---\n\nJob Description:\n\n{jd_content}"


def run_pipeline(prompt_file: Path, model: str, resume_text: str, jd: dict) -> dict:
    """Run all pipeline stages for one resume × JD pair."""

    def prompt(section: str) -> str:
        return load_prompt_section(prompt_file, section)

    ctx = shared_context(resume_text, jd["content"])
    result = {"label": jd["label"]}

    # Stage 0 — ATS simulation
    print("    [0/5] ats simulation...", file=sys.stderr)
    ats_ctx = f"Job Description:\n\n{jd['content']}\n\n---\n\nCandidate Resume:\n\n{resume_text}"
    ats = call_claude(f"{prompt('ats_simulation')}\n\n{ats_ctx}", model)
    result["ats_simulation"] = ats
    result["ats_verdict"] = extract_ats_verdict(ats)

    # Stage 1 — Reject gate
    print("    [1/5] reject gate...", file=sys.stderr)
    gate = call_claude(f"{prompt('reject_gate')}\n\n{ctx}", model)
    result["reject_gate"] = gate
    result["gate_verdict"] = extract_verdict(gate)

    if result["gate_verdict"].upper().startswith("KILL"):
        result["killed"] = True
        result["kill_oneliner"] = extract_kill_oneliner(gate)
        print("    KILLED — pipeline stops for this JD", file=sys.stderr)
        return result
    result["killed"] = False

    # Stage 2 — Signal quality
    print("    [2/5] signal quality...", file=sys.stderr)
    sq = call_claude(f"{prompt('signal_quality')}\n\n{ctx}", model)
    result["signal_quality"] = sq

    # Stage 3 — JD fit (chained: receives signal quality + gate concerns)
    print("    [3/5] jd fit...", file=sys.stderr)
    chained = (
        f"{prompt('jd_fit')}\n\n{ctx}\n\n---\n\n"
        f"Signal-quality calibration (prior stage):\n\n{sq}\n\n---\n\n"
        f"Rejection-gate output (address any CONDITIONAL PASS concerns):\n\n{gate}"
    )
    fit = call_claude(chained, model)
    result["jd_fit"] = fit
    result["fit_verdict"] = extract_verdict(fit)

    # Stage 4 — Candidate read
    print("    [4/5] candidate read...", file=sys.stderr)
    result["candidate_read"] = call_claude(f"{prompt('candidate_read')}\n\n{ctx}", model)

    # Stage 5 — TC brief stub
    print("    [5/5] tc brief stub...", file=sys.stderr)
    result["tc_brief"] = call_claude(f"{prompt('tc_brief_stub')}\n\n{ctx}", model)

    return result


def build_report(resume_path: Path, model: str, jd_results: list, run_date: str) -> str:
    lines = [
        f"# Resume Assessment — {resume_path.name}",
        f"**Model:** {model} | **Date:** {run_date}",
        "",
        "---",
        "",
        "## Triage Summary",
        "",
        "| Role | ATS | Gate | JD Fit |",
        "|------|-----|------|--------|",
    ]

    for r in jd_results:
        fit = "—" if r["killed"] else r.get("fit_verdict", "?")
        ats = r.get("ats_verdict", "—")
        gate = "KILL" if r["killed"] else r["gate_verdict"]
        lines.append(f"| {r['label']} | {ats} | {gate} | {fit} |")

    killed = [r for r in jd_results if r["killed"]]
    survivors = [r for r in jd_results if not r["killed"]]

    if killed:
        lines += ["", "---", "", "## Rejected at Gate", ""]
        for r in killed:
            ats_note = f" (ATS: {r['ats_verdict']})" if r.get("ats_verdict") else ""
            lines.append(f"- **{r['label']}**{ats_note}: {r['kill_oneliner']}")

    if survivors:
        lines += ["", "---", "", "## Per-Role Detail", ""]
        for r in survivors:
            lines += [
                f"### {r['label']}",
                "",
                r.get("ats_simulation", ""),
                "",
                r["reject_gate"],
                "",
                r["signal_quality"],
                "",
                r["jd_fit"],
                "",
                r["candidate_read"],
                "",
                r["tc_brief"],
                "",
                "---",
                "",
            ]

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Evaluate a resume against job descriptions using a 5-stage LLM pipeline."
    )
    parser.add_argument("--resume", required=True, help="Path to resume markdown file")
    parser.add_argument("--jd-dir", default=None, help=f"Directory of JD markdown files (default: {DEFAULT_JD_DIR})")
    parser.add_argument("--jd", default=None, help="Single JD file — skips --jd-dir")
    parser.add_argument("--output-dir", default=None, help=f"Output directory for reports (default: {DEFAULT_OUTPUT_DIR})")
    parser.add_argument("--prompts", default=None, help=f"Prompt file path (default: {DEFAULT_PROMPTS})")
    parser.add_argument(
        "--models",
        default="claude-sonnet-4-6",
        help=f"Comma-separated model IDs. Options: {', '.join(AVAILABLE_MODELS)}",
    )
    args = parser.parse_args()

    resume_path = Path(args.resume)
    if not resume_path.exists():
        print(f"Error: resume not found: {resume_path}", file=sys.stderr)
        sys.exit(1)

    prompt_file = Path(args.prompts) if args.prompts else DEFAULT_PROMPTS
    if not prompt_file.exists():
        print(f"Error: prompt file not found: {prompt_file}", file=sys.stderr)
        sys.exit(1)

    output_dir = Path(args.output_dir) if args.output_dir else DEFAULT_OUTPUT_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    models = [m.strip() for m in args.models.split(",")]
    for m in models:
        if m not in AVAILABLE_MODELS:
            print(f"Error: unknown model '{m}'. Options: {', '.join(AVAILABLE_MODELS)}", file=sys.stderr)
            sys.exit(1)

    if args.jd:
        jd_path = Path(args.jd)
        content = jd_path.read_text(encoding="utf-8")
        heading_match = re.match(r"^# (.+)", content)
        label = heading_match.group(1).strip() if heading_match else derive_label(jd_path)
        jds = [{"path": jd_path, "label": label, "content": content}]
    else:
        jd_dir = Path(args.jd_dir) if args.jd_dir else DEFAULT_JD_DIR
        jds = load_jds(jd_dir)

    resume_text = resume_path.read_text(encoding="utf-8")
    run_date = date.today().strftime("%Y%m%d")

    print(f"Resume:  {resume_path.name}", file=sys.stderr)
    print(f"JDs:     {len(jds)}", file=sys.stderr)
    print(f"Models:  {', '.join(models)}", file=sys.stderr)
    print(f"Output:  {output_dir}", file=sys.stderr)
    print("", file=sys.stderr)

    for model in models:
        slug = MODEL_SLUGS.get(model, model)
        print(f"Running {slug}...", file=sys.stderr)

        jd_results = []
        for i, jd in enumerate(jds, 1):
            print(f"  JD {i}/{len(jds)}: {jd['label']}", file=sys.stderr)
            jd_results.append(run_pipeline(prompt_file, model, resume_text, jd))

        report = build_report(resume_path, model, jd_results, run_date)
        base = f"assessment_{resume_path.stem}_{run_date}_{slug}"
        out_path = output_dir / f"{base}.md"
        if out_path.exists():
            v = 2
            while (output_dir / f"{base}_v{v}.md").exists():
                v += 1
            out_path = output_dir / f"{base}_v{v}.md"
        out_path.write_text(report, encoding="utf-8")
        print(f"  Saved: {out_path}", file=sys.stderr)

    print("\nDone.", file=sys.stderr)


if __name__ == "__main__":
    main()
