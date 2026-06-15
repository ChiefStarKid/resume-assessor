# resume-assessor

A five-stage LLM screening pipeline that evaluates a resume against job descriptions from a Talent Consultant's perspective.

If you're a recruiter, hiring manager, or job seeker who wants to know whether a resume would survive real screening — not just keyword matching — this pipeline reads it the way a sceptical human would: adversarially first, then charitably, then strategically.

Each stage is a discrete system prompt with a defined role, input, output format, and verdict logic. Stages chain output forward. The pipeline exits early on a hard rejection, so you only get the full read on candidates who clear the gate.

---

## Pipeline stages

| Stage | Name | Role | Early exit? |
|---|---|---|---|
| 0 | ATS Simulation | 15-second triage pass — keyword surface, title signal, recency, hard filters | No |
| 1 | Reject Gate | Adversarial — builds the strongest honest case for rejection | Yes, on KILL |
| 2 | Signal Quality | Calibrates how much to trust the CV — catches oversell and undersell | No |
| 3 | JD Fit | Floor check against core requirements; reads through Stage 2 calibration | No |
| 4 | Candidate Read | Surfaces hypotheses for interview — agency language, ceiling signals, friction | No |
| 5 | TC Brief Stub | Drafts the brief a recruiter would send to a hiring manager | No |

**Stage chaining:** Stage 3 (JD Fit) receives the full output of Stage 2 (Signal Quality) and Stage 1 (Reject Gate) as context — it reads the CV through the calibration, not in isolation.

---

## Sample output

Running `sample-resume.md` against `jds/example-senior-csm.md` with `claude-sonnet-4-6`:

```
## Triage Summary

| Role                              | ATS  | Gate             | JD Fit       |
|-----------------------------------|------|------------------|--------------|
| Senior Customer Success Manager   | Pass | CONDITIONAL PASS | Plausible fit|

### Reject Gate
**Verdict:** CONDITIONAL PASS

**Case for rejection:**
- No CS platform named beyond Gainsight — JD asks for Gainsight or similar, which is met,
  but depth of use is unverified
- Government sector experience mentioned once (GovTech migration) but not developed —
  JD lists it as a strong plus; thin as stated

**Why this isn't fatal:** The core NRR track record (114%, 108%) and enterprise scope
(S$2.4M ARR, CIO-level relationships) clear the primary bar. The government experience
gap is a soft concern, not a hard kill.

### Signal Quality
**Reliability:** High
**Primary risk:** None

**Oversell flags:** None — metrics carry context (ARR, seat count, specific outcomes).
**Undersell flags:** The GovTech migration is described in one clause. If that account
had meaningful complexity, it's probably understated.

**Calibration note:** Read the JD fit generously on the government experience gap —
there may be more there than stated.
```

Full sample report: [reports/example-assessment.md](reports/example-assessment.md)

---

## Quickstart

```bash
git clone https://github.com/ChiefStarKid/resume-assessor.git
cd resume-assessor
pip install -r requirements.txt

# Run against example files
python resume_assessor.py --resume sample-resume.md --jd jds/example-senior-csm.md

# Run against your own JD bank (all .md files in a directory)
python resume_assessor.py --resume path/to/resume.md --jd-dir path/to/jds/
```

---

## Requirements

- Python 3.9+
- [Claude Code CLI](https://claude.ai/code) installed and authenticated (`claude` on your PATH)
- No API key needed — uses `claude -p` (session auth from your Claude Code login)

```bash
pip install -r requirements.txt
```

`requirements.txt` is empty — the script uses only the standard library and the `claude` CLI. Install Claude Code and you're ready.

---

## Usage

```
python resume_assessor.py --resume RESUME [options]

Required:
  --resume PATH         Resume markdown file

JD input (one of):
  --jd PATH             Single JD markdown file
  --jd-dir DIR          Directory of JD markdown files (default: ./jds/)

Output:
  --output-dir DIR      Where reports are saved (default: ./reports/)
  --prompts PATH        Prompt file (default: assessor_prompt.md next to this script)

Model:
  --models MODEL[,...]  Comma-separated model IDs (default: claude-sonnet-4-6)
                        Options: claude-opus-4-8, claude-sonnet-4-6, claude-haiku-4-5-20251001
```

### Examples

```bash
# Single resume against single JD
python resume_assessor.py --resume resume.md --jd jds/role.md

# Single resume against all JDs in a folder
python resume_assessor.py --resume resume.md --jd-dir jds/

# Run two models in sequence, save to custom output folder
python resume_assessor.py \
  --resume resume.md \
  --jd-dir jds/ \
  --models claude-opus-4-8,claude-sonnet-4-6 \
  --output-dir results/

# Override prompt file
python resume_assessor.py --resume resume.md --jd jds/role.md --prompts my_prompts.md
```

---

## File format

**Resume** — any markdown file. The pipeline reads it as plain text.

**JD** — markdown file. The first `# Heading` becomes the role label in the report. Include a "Compulsory application questions" section if the role has screening questions — the pipeline uses them as explicit criteria in the reject gate and candidate read stages.

```markdown
# Role Title — Company Name

[JD body]

## Compulsory application questions

1. Question one
2. Question two
```

---

## How the prompts work

All stage prompts live in `assessor_prompt.md`, loaded by section header at runtime. Edit the prompt file freely — no code changes needed.

Each prompt defines:
- A discrete role with a single responsibility
- Input context (what it receives)
- Output format (structured markdown with named fields)
- Verdict tiers with explicit criteria

Stage 3 (JD Fit) is intentionally chained — it receives the full text output of Stages 1 and 2 in its context, so it reads the CV through the prior calibration rather than independently.

See [assessor_prompt.md](assessor_prompt.md) for the full prompt text.

---

## Adapting the pipeline

The pipeline is designed around a Talent Consultant (recruiter) reading a CV against a JD. It can be adapted:

- **Different screening criteria** — edit the `reject_gate` section in `assessor_prompt.md` to change what counts as a kill signal
- **Different output formats** — the output format block in each section is plain markdown; change the fields to match your workflow
- **Additional stages** — add a new `## [stage_name]` section to `assessor_prompt.md` and call `run_pipeline()` with the new section name
- **Different models per stage** — the `run_pipeline()` function takes a model argument; route heavy stages (reject gate, candidate read) to a more capable model if needed

---

## Repository structure

```
resume_assessor.py      — pipeline script
assessor_prompt.md      — all stage prompts, loaded by section at runtime
sample-resume.md        — fictional example resume for testing
jds/
  example-senior-csm.md — fictional example JD
reports/                — output directory (gitignored)
```

---

## Questions and feedback

- **General enquiries:** [joseph@kainosis.com](mailto:joseph@kainosis.com)
- **Bugs and feature requests:** [open an issue](../../issues)

## License

MIT
