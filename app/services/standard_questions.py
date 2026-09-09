from __future__ import annotations

# Phase 7: the dozen questions almost every ATS form asks. Seeded once into the
# question bank (empty answer, unapproved) so the user can answer them once
# during setup; `QuestionService.search_bank`'s token matcher (core.py) then
# recognizes common real-world phrasings of each and P4's auto-apply
# (`ApplicationService._auto_apply_bank_answers`) applies them silently.
#
# `keywords` is deliberately generous with function words: the tokenizer has
# no stopword list (`QuestionService._tokens`, core.py), so coverage == 1.0
# requires every token of a real phrasing -- "do", "you", "is", "the", and so
# on included -- to appear somewhere in canonical_question + keywords. Keep
# each entry's content words (the ones that actually distinguish it) free of
# overlap with unrelated topics like behavioral interview questions.
STANDARD_QUESTIONS: tuple[dict, ...] = (
    {
        "key": "work_authorization",
        "canonical_question": "Are you legally authorized to work in the United States?",
        "keywords": "this country do have legal right us eligible eligibility authorization visa sponsor",
    },
    {
        "key": "sponsorship",
        "canonical_question": "Will you now or in the future require sponsorship for employment visa status?",
        "keywords": "do to work our company need obtain maintain authorization visa",
    },
    {
        "key": "salary_expectation",
        "canonical_question": "What are your salary expectations?",
        "keywords": "is desired range you seeking for this role compensation pay wage expectation",
    },
    {
        "key": "start_date",
        "canonical_question": "When can you start?",
        "keywords": "what is your earliest available date how soon begin employment",
    },
    {
        "key": "relocation",
        "canonical_question": "Are you willing to relocate?",
        "keywords": "would be for this position open relocation",
    },
    {
        "key": "work_location_preference",
        "canonical_question": "Do you prefer remote, hybrid, or onsite work?",
        "keywords": "what is your location preference in office are looking for on site role",
    },
    {
        "key": "referral_source",
        "canonical_question": "How did you hear about this position?",
        "keywords": "where job opening find out our company",
    },
    {
        "key": "previously_applied",
        "canonical_question": "Have you previously applied to or worked for this company?",
        "keywords": "ever been employed by before in the past",
    },
    {
        "key": "security_clearance",
        "canonical_question": "Do you hold an active security clearance?",
        "keywords": "currently have government what level of",
    },
    {
        "key": "eeo_disclosure",
        "canonical_question": "Do you voluntarily wish to self-identify your veteran or disability status for EEO purposes?",
        "keywords": "as protected would like self identify",
    },
    {
        "key": "linkedin_portfolio_url",
        "canonical_question": "What is your LinkedIn profile URL?",
        "keywords": "please share link to portfolio or personal website github",
    },
    {
        "key": "years_experience",
        "canonical_question": "How many years of experience do you have in this role?",
        "keywords": "work what is your total number field",
    },
)
