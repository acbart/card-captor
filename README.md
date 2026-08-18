# card-captor

Local-only grading assistant for index-card quizzes.

Photograph a stack of 3×5 index cards, and card-captor detects each card, rectifies it,
reads the handwritten name / email / answer with Tesseract, matches the writer against your
roster, grades the answer, queues anything uncertain for human review, and exports
Canvas-ready CSVs.

Everything runs on your machine. There is no telemetry, no cloud OCR and no account. The only
outbound network calls are the optional Canvas grade upload and the optional
"import photos from a URL" helper — both are explicit, opt-in actions.

## Features

- **Card detection** — contour/quadrilateral detection tuned for index cards (OpenCV).
- **Perspective correction** — each detected card is warped to a flat rectangle.
- **Colour analysis** — mean Lab colour of the card stock, classified against a configurable
  set of named colours (useful when card colour encodes a group or a version of the quiz).
- **OCR** — pluggable engine interface with a Tesseract implementation; per-field
  preprocessing and page-segmentation modes for names, emails, free answers and MCQ letters.
- **Roster matching** — tiered exact/normalised/fuzzy email and name matching with explicit
  ambiguity detection (rapidfuzz).
- **Grading** — exact, case-insensitive, multi-accept, numeric-with-tolerance and MCQ rules.
- **Review queue** — flags for low confidence, unreadable answers, duplicate students,
  conflicting identities, unusual card colours and more.
- **Exports** — Canvas gradebook CSV plus a detailed audit CSV.
- **Canvas upload** — dry-run by default, idempotent via a payload hash; the API token is
  never logged.
- **Audit log** — every OCR result, instructor correction and upload is recorded.

## Requirements

- Python 3.11+
- [Tesseract OCR](https://tesseract-ocr.github.io/) on your `PATH`
  (`sudo apt install tesseract-ocr`, `brew install tesseract`, or
  [the Windows installer](https://github.com/UB-Mannheim/tesseract/wiki))

Tesseract is optional at runtime: if it is missing, the pipeline still detects, rectifies and
stores cards, but every field is returned with zero confidence and flagged for review.

## Installation

```bash
pip install -e ".[dev]"
```

This installs two console commands:

| Command | Description |
| --- | --- |
| `quizcards` | the CLI |
| `quizcards-web` | shortcut for starting the web UI |

## Quick start

```bash
quizcards create-course --name "CS 101" --term "Fall 2026"
quizcards create --course-id 1 --name "Week 3 Quiz" --date 2026-09-14 --points 1 --answers "B"
quizcards roster --activity-id 1 --csv roster.csv
quizcards import --activity-id 1 --folder ./photos/
quizcards process --activity-id 1
quizcards status --activity-id 1
quizcards export --activity-id 1 --output ./grades/
quizcards web            # http://127.0.0.1:8000
```

### CLI reference

```
quizcards create-course   Create a new course
quizcards create          Create a new activity
quizcards roster          Import a roster CSV into a course
quizcards import          Import photographs from a folder, ZIP archive or URL
quizcards process         Run the detection + OCR + grading pipeline
quizcards status          Show a summary of an activity
quizcards review          List the review queue and launch the web review UI
quizcards export          Export the Canvas grade CSV and the audit CSV
quizcards canvas-upload   Upload grades to Canvas (dry run by default)
quizcards web             Start the local web server
quizcards list            List courses or activities
```

Every command accepts `--activity-id` or `--activity-name` (and `--course-id` /
`--course-name` where relevant), and all of them go through the same service layer used by
the web UI, so the two front-ends can never drift apart.

Use `quizcards <command> --help` for the full option list.

### Roster CSV

Column names are normalised, so all of these work:

```csv
name,email,canvas_user_id,sis_id
Alice Johnson,alice.johnson@university.edu,1001,A1001
Bob Smith,bob.smith@university.edu,,A1002
```

```csv
Student Name,Email Address,SIS User ID
Alice Johnson,alice.johnson@university.edu,A1001
```

### Grading rules

`quizcards create` accepts `--rule` (`exact`, `case_insensitive`, `multi_accept`, `numeric`,
`mcq`), `--answers` (comma separated), `--tolerance` for numeric answers, `--normalize`
(`strip`, `lower`, `upper`, `remove_spaces`, `remove_punctuation`, `collapse_spaces`) and
`--choices` for the valid MCQ letters.

MCQ choices are deliberately separate from the accepted answers: the OCR character whitelist
is built from the *choices*, so recognition can never be biased toward the correct option.
More generally, constraints only re-rank OCR interpretations — they never inflate confidence.

## Web UI

```bash
quizcards web                 # 127.0.0.1:8000
quizcards web --port 9000
```

The server binds to `127.0.0.1` by default and has no authentication because it is meant to
be reachable only from your own machine. Passing `--host 0.0.0.0` exposes student data to
your network; do not do it unless you understand the consequences.

Screens: dashboard → activity → import → detected cards → review queue → card detail →
finalize (export + Canvas upload).

## Configuration

Settings come from environment variables (with or without the `CARDCAPTOR_` prefix) and from
a `.env` file in the working directory. Credentials are never written to the database or the
logs.

| Variable | Default | Purpose |
| --- | --- | --- |
| `CARDCAPTOR_DATA_DIR` | `~/.card-captor` | root for the database, originals, crops, exports, logs |
| `CARDCAPTOR_DB_PATH` | `<data_dir>/cardcaptor.db` | SQLite database location |
| `CARDCAPTOR_CANVAS_BASE_URL` | *(empty)* | e.g. `https://canvas.university.edu` |
| `CARDCAPTOR_CANVAS_TOKEN` | *(empty)* | Canvas API token — read from the environment only |
| `CARDCAPTOR_TESSERACT_PATH` | `tesseract` | path to the Tesseract binary |
| `CARDCAPTOR_OCR_CONFIDENCE_THRESHOLD` | `0.6` | below this a field is flagged unreadable |
| `CARDCAPTOR_MIN_CONFIDENCE_FOR_AUTO_REVIEW` | `0.8` | below this a submission needs review |
| `CARDCAPTOR_MIN_MATCH_CONFIDENCE` | `0.75` | below this a roster match needs review |
| `CARDCAPTOR_WEB_HOST` / `CARDCAPTOR_WEB_PORT` | `127.0.0.1` / `8000` | web server bind address |
| `CARDCAPTOR_IMPORT_ROOTS` | *(cwd, home, data dir)* | extra directories photos may be imported from (`os.pathsep` separated) |
| `CARDCAPTOR_ALLOW_PRIVATE_URL_IMPORT` | `0` | allow URL imports from private/loopback addresses |

Layout under the data directory:

```
originals/  imported photographs (never modified)
cards/      extracted card crops
exports/    generated CSVs
logs/       application logs
tmp/        scratch space for downloads and ZIP extraction
```

## Canvas upload

```bash
quizcards canvas-upload --activity-id 1                 # dry run, prints the payload
quizcards canvas-upload --activity-id 1 --no-dry-run    # actually uploads
```

Requires `CARDCAPTOR_CANVAS_BASE_URL`, `CARDCAPTOR_CANVAS_TOKEN` and a
`canvas_assignment_id` on the activity. Uploads are hashed and recorded, so re-running an
identical upload is skipped instead of duplicated. In `accumulated` mode the score is posted
together with a comment identifying the activity.

## Database migrations

The database is created automatically on first use. Alembic is available for schema upgrades:

```bash
alembic upgrade head
alembic revision -m "describe change"
```

`alembic.ini` reads the same configuration as the application, so `CARDCAPTOR_DATA_DIR`
selects which database is migrated.

## Privacy and data handling

- Student data never leaves your machine unless you explicitly run a Canvas upload.
- Raw OCR output is never overwritten; instructor corrections live in separate columns and
  are recorded in the audit log with the actor and timestamp.
- Imported photographs are stored unmodified and de-duplicated by SHA-256.
- ZIP imports are protected against path traversal and only extract image files; uploaded
  filenames are sanitised.
- Folder imports are restricted to the working directory, your home directory and the data
  directory (extend with `CARDCAPTOR_IMPORT_ROOTS`); URL imports refuse non-HTTP schemes,
  embedded credentials and private/loopback addresses.
- Exports written from the web UI stay inside the configured exports directory.
- The Canvas token is redacted in `repr()`, logs and audit entries.

## Development

```bash
pip install -e ".[dev]"
python -m pytest tests/ -v
```

Layout:

```
src/cardcaptor/
├── config.py        configuration and data directories
├── db/              SQLAlchemy models and session handling
├── vision/          card detection, perspective correction, colour analysis
├── ocr/             OCR engine interface and the Tesseract implementation
├── services/        activities, import, processing, roster, grading, review,
│                    export, Canvas and audit logging
├── cli/             Typer CLI
└── web/             FastAPI app, routers and Jinja2 templates
```
