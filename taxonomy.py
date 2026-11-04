"""Pain-point taxonomy for a UPI payments app + the rules/playbooks that hang off it.

Everything product-specific lives here so it can be edited without touching the pipeline:
  - CATEGORIES: label + keyword rules (English + common Hinglish) used by the offline extractor
  - CHURN_PATTERNS: phrases that signal the customer is leaving
  - PLAYBOOK: recommendation building blocks per category (change, owner, effort, dependencies, compliance)
"""
from __future__ import annotations


CATEGORIES = {
    "payment_failure": {
        "label": "Payment failure / pending debit",
        "keywords": [
            "deducted", "debited", "pending", "payment failed", "transaction failed", "failed transaction",
            "failed payment", "not credited", "stuck", "money gone", "paise kat", "paisa kat", "kat gaye",
            "kat gaya", "amount cut", "bank debited", "payment declined", "txn failed", "timed out", "timing out", "failed once", "failed again",
        ],
    },
    "refund_delay": {
        "label": "Refund delay",
        "keywords": [
            "refund", "reversal", "reversed", "money back", "paise wapas", "paisa wapas", "wapas nahi",
            "not refunded", "chargeback",
        ],
    },
    "onboarding_bank_linking": {
        "label": "Onboarding and bank linking",
        "keywords": [
            "otp", "sim binding", "sim verification", "bank not listed", "link bank", "linking my bank",
            "add bank", "bank account link", "registration", "sign up", "signup", "sign-up", "verify my number",
            "verification sms", "cannot register", "can't register", "unable to register", "setup", "set up",
            "account creation", "bank linking", "unable to link", "link my account", "can't link", "changing sim",
        ],
    },
    "upi_pin_auth": {
        "label": "UPI PIN and authentication",
        "keywords": [
            "upi pin", "pin reset", "reset pin", "forgot pin", "wrong pin", "incorrect pin", "set pin",
            "pin not", "biometric", "fingerprint", "debit card details", "pin change",
        ],
    },
    "kyc_limits": {
        "label": "KYC and limits",
        "keywords": [
            "kyc", "limit", "blocked my account", "account blocked", "account suspended", "account frozen",
            "aadhaar", "pan verification", "daily limit", "per day limit", "document rejected",
        ],
    },
    "rewards_cashback": {
        "label": "Rewards and cashback",
        "keywords": [
            "cashback", "reward", "scratch card", "coupon", "offer", "voucher", "points not", "cash back", "bonus",
        ],
    },
    "app_performance": {
        "label": "App performance and crashes",
        "keywords": [
            "crash", "crashes", "crashing", "freeze", "freezes", "freezing", "hang", "hangs", "slow",
            "lag", "laggy", "force close", "not opening", "won't open", "wont open", "loading forever",
            "keeps closing", "blank screen", "battery drain", "app band", "server down", "server is down",
            "network issue", "not working", "stopped working", "technical error", "glitch", "not open",
        ],
    },
    "customer_support": {
        "label": "Customer support",
        "keywords": [
            "customer care", "customer support", "support team", "no response", "no reply", "chatbot",
            "helpline", "complaint", "support ticket", "ticket status", "ticket id", "ticket number", "nobody replies", "agent", "escalat", "support nahi",
            "no one responds", "generic reply", "copy paste reply", "raised issue", "get response",
        ],
    },
    "fraud_scam": {
        "label": "Fraud and scams",
        "keywords": [
            "fraud", "scam", "scammer", "phishing", "collect request", "fake", "unauthorised", "unauthorized",
            "hacked", "cheated", "dhokha", "suspicious request", "spam call", "fake merchant", "stolen",
        ],
    },
    "merchant_qr": {
        "label": "Merchant and QR payments",
        "keywords": [
            "qr", "merchant", "shop", "scan", "settlement", "soundbox", "vendor", "dukaan", "scanner",
            "pos machine",
        ],
    },
    "ads_popups": {
        "label": "Ads, pop-ups and prompts",
        "keywords": [
            "pop-up", "pop up", "popup", "too many ads", "too much ad", "too much add", "ads everywhere",
            "has add", "asking for rating", "rate us", "promotional", "spam notification",
        ],
    },
    "fees_charges": {
        "label": "Fees and extra charges",
        "keywords": [
            "convenience fee", "platform fee", "processing fee", "extra fee", "extra charge", "extra amount",
            "service charge", "hidden charge", "charges", "charged", "pese lgte", "paise lagte", "paisa lagta",
            "pese lagte",
        ],
    },
    "ux_features": {
        "label": "UX and missing features",
        "keywords": [
            "confusing", "cluttered", "notification", "hard to find", "voiceover", "screen reader", "accessibility",
            "can't find", "cant find", "missing feature", "please add", "should add", "bill split",
            "split bill", "dark mode", "history search", "navigation", "interface", "redesign", "too many steps",
        ],
    },
}

CATEGORY_LABELS = {k: v["label"] for k, v in CATEGORIES.items()}

# Words that raise severity (money lost / task blocked / recurrence)
SEV_MONEY = ["deducted", "debited", "money", "paise", "paisa", "amount", "refund", "rs.", "rs ", "₹", "cashback"]
SEV_BLOCKED = ["can't", "cannot", "unable", "not able", "failed", "stuck", "blocked", "crash", "won't", "wont", "nahi ho"]
SEV_RECURRING = ["again", "every time", "third time", "second time", "still", "days", "weeks", "since", "repeatedly", "always"]

NEGATION_GUARD = ["no ", "not a ", "never had", "without any", "zero ", "haven't had", "hasn't had", "no more"]

CHURN_PATTERNS = [
    r"uninstall(?:ing|ed)?",
    r"(?:switch(?:ing|ed)?|moving|moved|shift(?:ing|ed)?|going) (?:over )?to (?:gpay|google pay|paytm|bhim|cred|navi|amazon pay|whatsapp pay)",
    r"last time (?:i|we)(?:'ll| will)? use",
    r"never (?:using|use) (?:this|it|the app) again",
    r"(?:delete|deleting|deleted) (?:the |this )?app",
    r"stopped using",
    r"(?:chhod|chod) (?:raha|rahi|diya|dunga|dungi)",
    r"(?:no longer|won't|will not|wont) (?:use|using) (?:this|it)",
    r"(?:looking for|need) (?:an )?alternative",
    r"(?:closing|close) my account",
    r"(?:use|using|prefer) (?:gpay|google pay|paytm|bhim) (?:instead|now|only)",
    r"(?:is this the )?(?:final|last) straw",
]

# Competitors watched in churn flags
COMPETITORS = ["gpay", "google pay", "paytm", "bhim", "cred", "navi", "amazon pay", "whatsapp pay"]

# Recommendation building blocks. effort: 1 (days) .. 5 (multi-quarter)
PLAYBOOK = {
    "payment_failure": {
        "change": "Show a live status tracker for pending debits with an expected auto-reversal time, and a one-tap 'raise dispute' action on the failed-payment screen.",
        "owner": "Payments + Platform",
        "effort": 3,
        "effort_text": "about 2 sprints",
        "dependencies": "Issuer bank and NPCI status codes; reconciliation service",
        "compliance": "Reversal timelines follow NPCI/RBI turnaround-time rules; confirm wording with Compliance.",
    },
    "refund_delay": {
        "change": "Add a refund tracker (initiated, with bank, credited) with the bank reference number and expected date; auto-notify when the refund lands.",
        "owner": "Payments + Support Ops",
        "effort": 3,
        "effort_text": "about 2 sprints",
        "dependencies": "Merchant and bank refund status feeds",
        "compliance": "Customer-facing refund timelines must match RBI turnaround-time (TAT) guidelines.",
    },
    "onboarding_bank_linking": {
        "change": "Add inline progress and clear error states to the bank-linking and SIM-binding steps, with a retry-OTP option and a 'bank not listed' fallback path.",
        "owner": "Growth + Payments",
        "effort": 2,
        "effort_text": "about 1-2 sprints",
        "dependencies": "Bank SDK and SMS gateway behaviour; device SIM permissions",
        "compliance": "SIM binding is a regulatory/security requirement; review any change with Security.",
    },
    "upi_pin_auth": {
        "change": "Redesign the UPI PIN set/reset flow with step-by-step guidance and specific error messages, and offer biometric re-auth where the bank supports it.",
        "owner": "Payments + Design",
        "effort": 3,
        "effort_text": "about 2 sprints",
        "dependencies": "NPCI Common Library flow; issuer bank support",
        "compliance": "PIN entry must stay inside the NPCI-certified secure component; no custom PIN capture.",
    },
    "kyc_limits": {
        "change": "Explain limits and KYC status in-app (what is blocked, why, what to do next) with a document re-upload flow and status tracker.",
        "owner": "Risk/Compliance + Product",
        "effort": 3,
        "effort_text": "about 2 sprints",
        "dependencies": "KYC vendor status API; risk rules team",
        "compliance": "KYC and limit rules are set by RBI/NPCI and the partner bank; copy needs Compliance sign-off.",
    },
    "rewards_cashback": {
        "change": "Show cashback status per transaction (eligible, pending, credited, expired) and state offer terms before payment.",
        "owner": "Growth / Rewards",
        "effort": 2,
        "effort_text": "about 1-2 sprints",
        "dependencies": "Rewards ledger; offer-terms content",
        "compliance": "Offer terms are promotional claims; Legal review for wording.",
    },
    "app_performance": {
        "change": "Triage crash clusters by app version and device; ship a hotfix for the top crash signature and add a startup-time budget to release gates.",
        "owner": "Mobile Engineering",
        "effort": 2,
        "effort_text": "about 1 sprint for the hotfix",
        "dependencies": "Crash analytics symbolication; device lab coverage",
        "compliance": "None specific.",
    },
    "customer_support": {
        "change": "Add a 'Track my complaint' view, route payment disputes to a human within a fixed SLA, and replace generic bot replies with issue-specific answers.",
        "owner": "Support Ops + Product",
        "effort": 3,
        "effort_text": "about 2 sprints plus process change",
        "dependencies": "Ticketing tool integration; agent staffing",
        "compliance": "Grievance redressal timelines apply (RBI ombudsman scheme); confirm SLA wording.",
    },
    "fraud_scam": {
        "change": "Add in-flow warnings on collect requests and unknown payees, a one-tap 'report and block' action, and an education card about common scams.",
        "owner": "Risk + Product",
        "effort": 3,
        "effort_text": "about 2 sprints",
        "dependencies": "Risk scoring signals; NPCI fraud-reporting channel",
        "compliance": "Fraud reporting and liability rules apply; Security and Compliance must review.",
    },
    "merchant_qr": {
        "change": "Improve QR scan reliability (low light, damaged codes) and show a clear 'paid to merchant' confirmation with settlement status for merchants.",
        "owner": "Merchant Products",
        "effort": 3,
        "effort_text": "about 2 sprints",
        "dependencies": "Camera/scan library; merchant settlement service",
        "compliance": "Merchant settlement timelines depend on acquiring bank agreements.",
    },
    "ads_popups": {
        "change": "Cap promotional pop-ups per session, never interrupt a payment flow with a product offer, and stop repeating the rating prompt after a user has rated or dismissed it.",
        "owner": "Growth + Design",
        "effort": 2,
        "effort_text": "about 1-2 sprints",
        "dependencies": "Campaign and in-app messaging service; frequency-capping rules",
        "compliance": "Financial product promotions (loans, investments) carry disclosure rules; Legal reviews wording and placement.",
    },
    "fees_charges": {
        "change": "Show any fee before the user confirms (amount, reason, and how to avoid it), and add a plain-language fees page linked from the payment and recharge screens.",
        "owner": "Payments + Finance",
        "effort": 2,
        "effort_text": "about 1-2 sprints",
        "dependencies": "Pricing service; fee display on recharge, bill pay and card flows",
        "compliance": "Fee disclosure must be clear at the point of payment; Compliance confirms wording and any regulatory limits.",
    },
    "ux_features": {
        "change": "Run a quick usability pass on the home screen and history search; prioritise the most requested missing features (for example bill split) for discovery.",
        "owner": "Design + Product",
        "effort": 2,
        "effort_text": "about 1-2 sprints for discovery",
        "dependencies": "Design capacity; research input",
        "compliance": "None specific.",
    },
}
