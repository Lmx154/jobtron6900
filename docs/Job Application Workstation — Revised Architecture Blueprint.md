# Job Application Workstation — Revised Architecture Blueprint

> **Historical (2026-10-16).** This is the original blueprint. The current product is a narrower local application tracker that drafts only answers and cover letters; see `README.md` and `CLAUDE.md`.

## 1. Product Definition

A simple, local-first job application workspace that stores everything known about the user and every application they submit.

The application exposes that information through one MCP server so any compatible model can read, enrich, and update the same underlying data.

The user reviews the final materials in the UI and manually fills out and submits the employer's application.

The product exists to support one loop:

**Know me → understand the job → prepare the application → review it → apply manually → preserve what was submitted → track the result.**

It is not:

- an HR workspace
- an ATS
- a job scraper
- an email client
- a browser extension
- an automatic application submitter
- an AI-provider platform
- an agent orchestration framework
- a cloud SaaS product

---

# 2. Core Philosophy

## Simplicity is an architectural requirement

Every major component should be understandable without tracing through multiple languages, services, queues, or APIs.

The entire application should remain mentally reducible to:

```text
Browser UI
    │
    ▼
Python Application
    │
    ├── SQLite
    ├── Local Files
    └── MCP Server
```

That is the system.

---

# 3. Core Design Rules

## Rule 1 — One source of truth

SQLite owns the structured data.

The filesystem owns binary files.

Models do not maintain hidden application state.

Both the UI and MCP operate on the same underlying application services.

---

## Rule 2 — One application core

There must not be separate implementations for:

- UI operations
- local AI operations
- Codex operations
- Claude operations
- MCP operations

Instead:

```text
             Application Services
                /          \
             Web UI         MCP
```

Both interfaces call the same Python functions.

---

## Rule 3 — One MCP surface

OpenCode, Codex, Claude Code, or another compatible client all see the same MCP tools and resources.

The application does not care what model is connected.

There is no:

- local-model MCP
- large-model MCP
- model-specific toolset
- automatic routing
- escalation hierarchy

Model choice belongs to the user.

---

## Rule 4 — Models provide intelligence

The application should not recreate capabilities modern models already have.

Do not build complex internal systems for:

- resume interpretation
- screenshot OCR
- application-question extraction
- job-description analysis
- candidate/job matching
- cover-letter writing
- resume tailoring
- application-answer generation

The model performs those tasks.

The app provides:

- structured information
- file access
- persistence
- editing
- version history
- human review

---

## Rule 5 — Human review remains final

Models may create or improve content.

The user sees the result in the interface and decides whether to:

- accept it
- edit it
- replace it
- ignore it

The employer's application remains manually submitted by the user.

---

## Rule 6 — Preserve submitted history

Changing something later must not rewrite history.

If Application 42 used Resume Version 3 and Cover Letter Version 2, those versions remain associated with Application 42 permanently.

We should always be able to determine exactly what was submitted.

---

## Rule 7 — No infrastructure without immediate value

Before adding anything, ask:

> Does this make applying faster, improve the application, preserve important information, or help manage applications?

If the answer is no, it stays out.

---

# 4. Recommended Technology Stack

## Language

**Python**

Why:

- excellent document ecosystem
- excellent image/PDF support
- natural fit for structured data
- excellent MCP support
- simple SQLite integration
- easy analytics later
- low development overhead
- highly understandable to coding models
- one language can power almost the entire system

Raw systems-language performance provides little value here.

---

## Web server

**FastAPI**

Responsibilities:

- local application server
- HTML routes
- form/file submissions
- lightweight JSON endpoints when useful
- startup/shutdown
- application service wiring

The server binds only to localhost by default.

Example:

```text
127.0.0.1:8765
```

---

## UI

**Jinja2 + HTMX**

Use server-rendered HTML as the default.

Use HTMX only where partial page updates improve usability.

Examples:

- changing application status
- adding questions
- refreshing event lists
- approving document versions
- uploading screenshots
- updating application completeness

Avoid building a SPA until a concrete UI requirement demands one.

---

## JavaScript

**Minimal vanilla JavaScript**

Only where the browser genuinely requires it.

Likely uses:

- clipboard image paste
- drag-and-drop
- calendar interactions
- document previews
- small UI conveniences

There should not be a large frontend application state layer.

---

## Database

**SQLite**

One database file.

No:

- PostgreSQL
- Redis
- Elasticsearch
- vector database
- cloud database

SQLite is more than sufficient for a single-user local application.

---

## Database access

Use a deliberately thin layer.

Recommended starting point:

**SQLModel or lightweight SQLAlchemy usage**

Use it for:

- schema definitions
- basic relationships
- migrations
- typed data access

Do not build a complicated repository abstraction hierarchy.

If the ORM begins creating more complexity than it removes, plain SQLite queries remain acceptable.

---

## Validation and schemas

**Pydantic**

Use shared typed structures at boundaries such as:

- forms
- MCP inputs
- MCP outputs
- application context objects
- imports

---

## MCP

**Python MCP SDK / implementation**

MCP should live inside the same project and call the same application services as the web UI.

MCP is a first-class interface, not a bolt-on integration.

---

## Files

Use the normal filesystem.

Recommended conceptual layout:

```text
data/
├── app.db
└── files/
    ├── profile/
    │   ├── resumes/
    │   └── other/
    │
    └── applications/
        └── <application-id>/
            ├── assets/
            ├── resumes/
            ├── cover-letters/
            └── other/
```

The database stores relationships and metadata.

The filesystem stores the bytes.

---

## Images

Use **Pillow** only where useful for:

- thumbnails
- dimensions
- basic conversion
- previews

The app does not need to implement computer vision.

---

## PDFs

Use **PyMuPDF** or similarly lightweight tooling for:

- PDF metadata
- page rendering when required
- text extraction when useful
- previews

The multimodal model can inspect rendered pages through MCP when needed.

---

## Documents

Add document libraries only when actually needed.

Possible future examples:

- python-docx
- HTML → PDF tooling

Do not install a huge document-processing stack preemptively.

---

# 5. Entire System Architecture

```text
                       USER
                        │
                        ▼
              ┌───────────────────┐
              │  Browser          │
              │                   │
              │ localhost UI      │
              └─────────┬─────────┘
                        │
                        ▼
        ┌─────────────────────────────────┐
        │        Python Application       │
        │                                 │
        │ FastAPI                         │
        │ Jinja2                          │
        │ HTMX endpoints                  │
        │                                 │
        │ Application Services            │
        │                                 │
        │ Candidate                       │
        │ Applications                    │
        │ Questions                       │
        │ Documents                       │
        │ Assets                          │
        │ Events                          │
        │ Context Assembly                │
        └───────────┬───────────┬─────────┘
                    │           │
                    ▼           ▼
                 SQLite      Local Files
                    │
                    │
                    ▼
             ┌──────────────┐
             │  MCP Server  │
             └──────┬───────┘
                    │
          any compatible MCP client
                    │
        OpenCode / Codex / Claude Code
```

There is no internal AI provider.

There are no API keys.

---

# 6. Suggested Project Structure

Keep the repository shallow and obvious.

```text
job-workstation/
│
├── app/
│   ├── main.py
│   │
│   ├── db.py
│   ├── models.py
│   ├── schemas.py
│   │
│   ├── services/
│   │   ├── candidate.py
│   │   ├── applications.py
│   │   ├── documents.py
│   │   ├── questions.py
│   │   ├── assets.py
│   │   └── events.py
│   │
│   ├── routes/
│   │   ├── dashboard.py
│   │   ├── profile.py
│   │   ├── applications.py
│   │   └── assets.py
│   │
│   ├── mcp/
│   │   ├── server.py
│   │   └── tools.py
│   │
│   ├── templates/
│   │
│   └── static/
│
├── data/
│
├── tests/
│
└── pyproject.toml
```

Do not introduce extra packages/modules merely because conventional enterprise architecture suggests them.

---

# 7. Application Services

This is the most important internal boundary.

The UI and MCP should never duplicate core operations.

Example conceptual services:

```text
CandidateService
ApplicationService
QuestionService
DocumentService
AssetService
EventService
```

Example:

```text
ApplicationService.get_application(id)

ApplicationService.get_context(id)

ApplicationService.create(...)

ApplicationService.update(...)
```

The web interface calls those functions.

The MCP tools call those exact same functions.

---

# 8. Minimal Data Model

Aim for approximately 10–12 meaningful tables.

Do not normalize every tiny concept into its own table.

---

## Candidate

One primary candidate record.

Fields may include:

- ID
- name
- email
- phone
- location
- website
- GitHub
- LinkedIn
- professional summary

---

## Candidate Facts

The structured long-term knowledge base.

Fields:

- ID
- category
- title
- description
- tags
- source
- verified
- created_at
- updated_at

Possible categories:

```text
experience
project
education
skill
achievement
leadership
technical_story
reusable_answer
preference
other
```

Examples:

```text
Project:
MARV flight computer
```

```text
Skill:
Hardware-in-the-loop validation
```

```text
Technical story:
Diagnosed unreliable telemetry during rocket testing
```

These facts become reusable application context.

---

# 9. Master Resume

The user has one canonical master resume.

Store:

- file
- extracted text when available
- version
- created date
- active/inactive

Do not overwrite old master versions.

Application resumes derive from the master but remain independent documents afterward.

---

# 10. Companies

Very small table.

Fields:

- ID
- name
- notes

No company enrichment.

No organizational database.

---

# 11. Applications

The central object.

Fields:

- ID
- company ID
- formal position title
- normalized role
- role family
- URL
- location
- job description
- status
- date created
- date applied
- notes

Example:

```text
Formal Title:
Junior Software Engineer I

Normalized Role:
Software Engineer

Role Family:
Software Engineering
```

Never discard the employer's formal title.

---

# 12. Role Normalization

Role normalization exists only to improve organization and analytics.

Keep it simple.

Examples:

```text
Junior Software Engineer I
        ↓
Software Engineer
        ↓
Software Engineering
```

```text
Flight Software Engineer
        ↓
Embedded Software Engineer
        ↓
Software Engineering
```

The model may suggest classifications.

The application simply stores the result.

Do not build an elaborate taxonomy engine.

---

# 13. Questions

Application questions belong directly to applications.

Fields:

- ID
- application ID
- question
- field type
- allowed choices
- required
- character limit
- order

Possible field types:

```text
text
long_text
yes_no
choice
multi_choice
number
date
other
```

---

# 14. Answers

Questions may have multiple answer versions.

Fields:

- ID
- question ID
- content
- version
- approved
- source label
- created_at

Example history:

```text
v1 manually written
v2 Qwen revision
v3 GPT revision
v4 final approved
```

No model-specific logic is required.

`source_label` is simply metadata.

---

# 15. Documents

All application documents use one general document model.

Possible types:

```text
master_resume
application_resume
cover_letter
transcript
portfolio
writing_sample
other
```

Fields:

- ID
- application ID if applicable
- document type
- title
- file path
- text representation if available
- version
- approved
- created_at

---

# 16. Assets

Assets are raw information supplied by the user.

Examples:

- screenshots
- employer PDFs
- resume images
- job posting screenshots
- application screenshots
- miscellaneous supporting files

Fields:

- ID
- application ID or candidate scope
- type
- title
- MIME type
- path
- notes
- created_at

Assets are intentionally generic.

The model determines what they mean.

---

# 17. Events

Events create both the application timeline and dashboard calendar.

Fields:

- ID
- application ID
- type
- date/time
- notes

Typical event types:

```text
Applied
Assessment
Recruiter Contact
Phone Screen
Interview
Technical Interview
Onsite
Follow-up
Offer
Rejected
Withdrawn
Custom
```

---

# 18. Contacts

Simple optional records.

Fields:

- ID
- application ID
- name
- title/role
- email
- phone
- notes

No Gmail.

No automated outreach.

---

# 19. Application Status

Keep statuses simple.

Recommended:

```text
Draft
Ready to Apply
Applied
Assessment
Interviewing
Offer
Rejected
Withdrawn
Closed
```

User controls status.

Do not create a formal workflow engine.

---

# 20. Application Completeness

Completeness should be derived from existing data.

Example:

```text
Job Description          ✓
Application Resume       ✓
Cover Letter             ✓
Questions Found          13
Questions Answered       11
Application Submitted    ✗
```

No internal state machine is required.

---

# 21. Main UI Areas

Only five primary navigation areas are needed.

```text
Dashboard
Profile
Applications
Application Workspace
Calendar
```

MCP status can live in Settings or a small utility page rather than becoming another giant workspace.

---

# 22. Dashboard

Keep the dashboard useful and minimal.

Show:

- active applications
- applications this month
- interviewing
- offers
- rejections
- upcoming events
- calendar preview
- recent applications

Nothing HR-like.

---

# 23. Profile

Profile contains:

- basic information
- master resume
- candidate facts
- projects
- experience
- skills
- achievements
- reusable stories

The database representation may use candidate facts underneath, while the UI groups them by category.

---

# 24. Applications List

Each row/card needs only useful information.

Example:

```text
RTX
Embedded Software Engineer I

Applied Aug 28
Interviewing

[Open]
```

Filters may eventually include:

- status
- company
- role family
- date

Do not start with elaborate analytics.

---

# 25. Application Workspace

This is the primary working area.

A single page should provide the entire application package.

Example:

```text
RTX
Embedded Software Engineer I

Status: Draft
URL: ...

--------------------------------
JOB
--------------------------------

Job Description
Role
Location
Notes

--------------------------------
APPLICATION RESUME
--------------------------------

Current version
Previous versions
Approved status

--------------------------------
COVER LETTER
--------------------------------

Current version
Previous versions
Approved status

--------------------------------
APPLICATION QUESTIONS
--------------------------------

Question 1
Answer

Question 2
Answer

Question 3
Answer

--------------------------------
FILES / SCREENSHOTS
--------------------------------

application-page-1.png
application-page-2.png
job-posting.pdf

--------------------------------
EVENTS
--------------------------------

Applied
Interview
Follow-up
etc.
```

The user should rarely need to leave this screen while working on an application.

---

# 26. Universal Input

Provide a simple input area for:

```text
Paste text
Paste screenshot
Upload image
Upload PDF
Upload document
Drop file
```

The user attaches the input to either:

```text
My Profile
```

or:

```text
Application X
```

That's enough.

Do not build separate import wizards for every content type.

---

# 27. Screenshot and Vision Workflow

The application does not implement CV.

Workflow:

```text
User pastes screenshot
        ↓
Asset is stored
        ↓
Model retrieves asset through MCP
        ↓
Model visually interprets screenshot
        ↓
Model writes structured information through MCP
        ↓
UI displays result
```

Example:

A screenshot contains:

```text
Why are you interested in this position?

Describe your experience with embedded systems.
```

The model creates two question records.

No OCR pipeline exists in the application.

---

# 28. Resume Ingestion

Same philosophy.

User uploads resume.

The model can read:

```text
candidate context
resume file/text
```

and write candidate facts.

Example:

```text
Avionics leadership
Firmware validation
Rust
Embedded systems
PCB design
Hardware-in-the-loop testing
```

These appear in the UI.

The user can correct them.

---

# 29. Job Ingestion

The user can provide:

- pasted description
- screenshot
- PDF
- copied employer listing

The model interprets the input and updates the application.

Possible extracted information:

- company
- formal title
- location
- description
- requirements
- preferred skills
- role family

No scraper is required.

---

# 30. MCP Philosophy

MCP exposes application concepts.

It should not expose raw SQL.

Bad:

```text
execute_sql
update_row
query_database
```

Good:

```text
candidate.get_context
applications.get_context
questions.add
answers.save
documents.save
events.add
```

The model should not need to understand the schema.

---

# 31. MCP Read Surface

Initial reads:

```text
candidate.get_profile
candidate.get_facts
candidate.get_context

applications.list
applications.get
applications.get_context

documents.list
documents.get

questions.list

assets.list
assets.get

events.list
```

Do not create dozens of narrow read tools unless a concrete problem appears.

---

# 32. The Most Important MCP Operation

## `applications.get_context`

This should provide a coherent application package.

Conceptual result:

```text
APPLICATION

Company
Role
URL
Location
Job Description

CANDIDATE

Profile
Relevant Facts
Master Resume

APPLICATION MATERIALS

Application Resume
Cover Letter

QUESTIONS

Question 1
Current Answer

Question 2
Current Answer

ASSETS

Available screenshots/files

EVENTS

Timeline
```

This allows a model to immediately understand an application without manually joining tables.

---

# 33. MCP Write Surface

Start with:

```text
candidate.add_fact
candidate.update_fact

applications.create
applications.update

questions.add
questions.update

answers.save

documents.save
documents.approve

events.add
events.update
```

Assets are normally uploaded through the UI, although MCP support can be added if useful later.

---

# 34. MCP and Files

Models must be able to access:

- text
- images
- PDFs
- application documents

The MCP layer should provide enough information for a compatible client to consume the resource.

Do not duplicate the model's multimodal capabilities.

---

# 35. No Built-In Model Configuration

The application should not contain:

```text
OpenAI API key
Anthropic API key
Gemini key
OpenRouter key
provider dropdown
model marketplace
```

OpenCode handles local models.

Codex handles OpenAI access.

Claude Code handles Anthropic access.

The app exposes MCP.

That's the entire relationship.

---

# 36. Optional Model Recommendation Flags

The UI may give lightweight advice.

Example:

```text
Task:
Extract questions from this screenshot

Suggested:
Local model should be sufficient.
```

Or:

```text
Task:
Final holistic application review

Suggested:
A larger model may provide better nuanced feedback.
```

These are recommendations only.

No routing takes place.

---

# 37. Versioning

Version only the content where history matters.

Important versioned objects:

- master resume
- application resume
- cover letter
- application answers

Do not build generic event-sourced versioning for every database row.

---

# 38. Approval

Useful generated content may have:

```text
approved = true/false
```

That's enough initially.

No multi-stage approval workflow.

---

# 39. Search

Search is not required immediately.

When added, start with normal SQLite text search.

Do not introduce embeddings simply because models are involved.

If ordinary search later proves insufficient, revisit it then.

---

# 40. Analytics

Not part of the first architecture milestone.

Eventually Python makes questions like these straightforward:

```text
Applications per month

Interview rate by role family

Average days to response

Applications by company

Offer rate

Embedded vs general SWE interview rate

Most frequent application questions
```

SQLite + Python is enough.

---

# 41. Testing Philosophy

The project needs testable early wins rather than one huge implementation.

Tests should emphasize real user workflows.

Each milestone should end with a scenario that proves the architecture is useful.

---

# 42. WIN 1 — Application Notebook

Build:

- FastAPI
- SQLite
- Jinja2
- applications
- events
- basic dashboard
- calendar
- application detail page

No MCP.

No document intelligence.

No AI.

### Test

Create three fake applications.

For each store:

- company
- title
- URL
- job description
- status
- application date

Add events.

Verify:

- application list works
- details persist
- calendar works
- events link back to applications

### Architectural question answered

> Is our basic application data model pleasant to use?

If not, fix it before proceeding.

---

# 43. WIN 2 — Candidate Profile

Add:

- profile
- candidate facts
- master resume
- profile UI

### Test

Represent a realistic candidate completely enough to support an application.

Verify:

- facts are easy to add/edit
- resume is visible
- candidate data does not feel duplicated

### Architectural question answered

> Can the app represent what the user knows about themselves simply?

---

# 44. WIN 3 — Read-Only MCP

This is the most important architectural proof.

Expose only:

```text
candidate.get_context

applications.list

applications.get_context
```

### Test

Connect OpenCode.

Ask:

```text
Review application 3 and tell me how well my background matches the position.
```

The user should not manually paste:

- resume
- job description
- profile
- application information

The model gets everything through MCP.

### Architectural question answered

> Does MCP provide a clean enough view of the application that external models can reason about it naturally?

If the answer is yes, the central architecture works.

---

# 45. WIN 4 — Questions and Answers

Add:

- application questions
- answers
- question UI

Add MCP:

```text
questions.add

answers.save
```

### Test

Copy five questions from a real application.

Have OpenCode answer them using application context.

Answers should immediately appear in the UI.

### Architectural question answered

> Can a model meaningfully enrich an application through MCP?

At this point the product is already useful.

---

# 46. WIN 5 — Screenshot Assets

Add:

- image upload
- clipboard screenshot paste
- asset list
- MCP asset access

### Test

Take a screenshot containing application questions.

Tell a multimodal model:

```text
Read the latest screenshot for application 3 and add all visible questions.
```

Verify:

- model can see the image
- questions are extracted correctly
- records appear in the UI

### Architectural question answered

> Can we use model vision without building any CV infrastructure ourselves?

This is a critical proof.

---

# 47. WIN 6 — Application Documents

Add:

- application resume
- cover letter
- document versions
- approval

Expose documents through MCP.

### Test

Ask:

```text
Review application 3 and create a tailored resume.
```

Then:

```text
Create a cover letter.
```

Both should appear inside the application workspace.

### Architectural question answered

> Can MCP models produce application materials that become first-class application records?

---

# 48. WIN 7 — Full Application Package

Build the consolidated review view.

It should show:

```text
Job
Candidate match
Resume
Cover letter
Questions
Answers
Assets
Notes
Missing information
```

### Test

Ask:

```text
Audit application 3 completely.
```

The model retrieves the entire application through MCP and reports:

- weak resume areas
- missing experience
- weak answers
- contradictions
- missed requirements
- cover-letter issues

User reviews everything in the UI.

### Architectural question answered

> Is this actually a complete application workstation?

If yes, the core product is effectively complete.

---

# 49. WIN 8 — Submitted Application Archive

Add final archival behavior.

When marked Applied, preserve:

- application date
- final resume version
- final cover letter
- final answers
- job description
- application URL
- application assets

### Test

Return to an old application later.

The application must show exactly what was used when applying.

### Architectural question answered

> Can the product function as a reliable job-application catalogue?

---

# 50. WIN 9 — Outcome Tracking

Add:

- contacts
- interviews
- offers
- rejection
- follow-ups
- additional events

### Test

Track one application from:

```text
Draft
→ Applied
→ Recruiter Contact
→ Interview
→ Technical Interview
→ Offer/Rejected
```

Verify all events appear correctly on:

- application timeline
- dashboard
- calendar

---

# 51. Explicit Non-Goals for v1

Do not implement:

- Gmail
- Outlook
- automatic email parsing
- company enrichment
- job-board APIs
- job scraping
- Workday integrations
- Greenhouse integrations
- browser extensions
- browser automation
- automatic form filling
- automatic submission
- API-key configuration
- built-in OpenAI support
- built-in Anthropic support
- model routers
- autonomous agents
- orchestration engines
- escalation systems
- vector databases
- embedding pipelines
- RAG infrastructure
- cloud accounts
- user authentication
- multiple users
- team features
- HR workflows
- recruiter workflows
- ATS features
- notification systems
- plugin frameworks
- microservices
- desktop packaging
- Kubernetes
- queues
- background-worker architecture

If one of these later solves a real observed problem, reconsider it then.

---

# 52. Packaging Strategy

Initially:

```text
job-workstation
```

starts the FastAPI application.

Then the user opens:

```text
http://localhost:8765
```

No desktop packaging is required.

Later, if desired, provide:

- launcher
- desktop shortcut
- system tray
- packaged executable

Those are distribution improvements, not application architecture.

---

# 53. Backup Strategy

Keep it simple because the entire application is valuable personal history.

A complete backup consists mainly of:

```text
app.db
files/
```

Later provide:

```text
Export Backup
```

which creates one archive.

No cloud sync required.

---

# 54. Architectural Constraints

The following should remain true whenever possible:

### One Python process

Avoid splitting the application into multiple required services.

### One database

SQLite.

### One source tree

No separate frontend/backend repositories.

### One MCP interface

Every model sees the same app.

### One application context model

UI and MCP use the same underlying representation.

### No AI dependencies inside the application core

Models remain external MCP clients.

---

# 55. Complexity Warning Signs

Stop and reconsider if implementation starts requiring concepts like:

```text
AI orchestration layer
workflow coordinator
task queue
model router
context broker
event bus
provider abstraction
agent registry
knowledge graph
embedding service
distributed worker
frontend state store
```

None should be necessary for the defined product.

---

# 56. Architectural Litmus Tests

Before approving a new feature, ask four questions.

### 1.

Does it directly help:

```text
prepare
review
submit
remember
or manage
```

a job application?

### 2.

Can it be implemented using the existing Python app, SQLite, files, and MCP?

### 3.

Are we duplicating a capability that modern models already provide?

### 4.

Will adding it make the codebase substantially harder for a developer or coding model to understand?

If the answers look bad, do not add it.

---

# 57. Definition of v1

Version 1 is complete when the following workflow works well:

1. User opens the local web application.

2. User enters their basic profile.

3. User uploads a master resume.

4. Candidate facts can be stored and edited.

5. User creates an application.

6. User supplies the company's formal role title, URL, and job description.

7. An MCP-connected model can retrieve the complete candidate context.

8. The same model can retrieve the complete application context.

9. User can paste screenshots and upload files into the application.

10. A multimodal model can read those screenshots/files through MCP.

11. The model can populate application questions.

12. The model can save answers.

13. The model can create or revise an application-specific resume.

14. The model can create or revise a cover letter.

15. The model can audit the complete application package.

16. User can review all resulting information from one application workspace.

17. User manually fills out the employer's application.

18. User marks the application Applied.

19. The exact application materials remain archived.

20. Interviews, assessments, offers, rejections, and other events can be recorded.

21. Those events appear on the timeline and calendar.

22. The application remains useful months later as a historical record.

Once this works well:

**stop building architecture and start using the product.**

Real usage should determine everything afterward.

---

# 58. Final Architecture

The final mental model should remain this simple:

```text
                 USER
                  │
                  ▼
             Web Browser
                  │
               localhost
                  │
                  ▼
         ┌─────────────────┐
         │ Python App      │
         │                 │
         │ FastAPI         │
         │ Jinja2 + HTMX   │
         │ Services        │
         └───────┬─────────┘
                 │
          ┌──────┴──────┐
          │             │
        SQLite        Files
          │             │
          └──────┬──────┘
                 │
             MCP Server
                 │
       Any compatible client
                 │
    OpenCode / Codex / Claude Code
```

**Python handles the application.**

**SQLite handles structured truth.**

**Files handle documents and screenshots.**

**MCP exposes the system.**

**Models supply intelligence.**

**The user supplies judgment.**

Nothing more complicated should exist until real-world usage proves that it needs to.