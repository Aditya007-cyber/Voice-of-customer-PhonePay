"""Generate a SYNTHETIC multi-source feedback dataset for a UPI app, with generator-side gold labels.

Why synthetic: support tickets, sales calls and interviews are private data. This lets the whole
pipeline + eval harness run end to end. Every record carries is_synthetic=true and the dashboard says so.

The mix deliberately includes things that break naive pipelines: PII, Hinglish, praise-only reviews,
spam, sarcasm, complaints phrased without any obvious keyword, negated keywords ("no failed
transactions") and false churn triggers. So eval scores will NOT be 100%.

Usage: python scripts/make_synthetic.py [--n 420] [--seed 7]
"""
from __future__ import annotations

import argparse
import csv
import json
import random
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

BANKS = ["SBI", "HDFC", "ICICI", "Axis", "Kotak", "PNB", "Canara", "Bank of Baroda", "Union Bank"]
AMOUNTS = ["200", "450", "1,200", "2,500", "5,000", "850", "3,000", "12,000", "600"]
DAYS = ["2", "3", "4", "5", "7", "10"]

T = {
    "payment_failure": [
        "Rs {amt} got deducted from my {bank} account but the payment shows pending. Still not credited to the receiver.",
        "Transaction failed but money was debited. This is the {nth} time it has happened this month.",
        "Payment stuck on processing for {days} days and my amount is gone from the bank.",
        "Paise kat gaye lekin transaction failed dikha raha hai. Kab milega wapas?",
        "Every time I pay a bill the payment is declined and then Rs {amt} is deducted anyway.",
        "UPI payment keeps timing out. Bank debited me but the merchant says not received.",
        "Amount cut from my account, status still pending after {days} days. Very frustrating.",
        "Payment failed again at the last step but {bank} already debited the money.",
    ],
    "refund_delay": [
        "Still waiting for my refund of Rs {amt}. It has been {days} days since the payment failed.",
        "Refund not received even after the merchant confirmed the reversal.",
        "Order cancelled but no money back yet. {days} days and nothing in my {bank} account.",
        "Mera refund abhi tak nahi aaya. Paise wapas kab aayenge?",
        "The app says refund initiated but my bank shows nothing after {days} days.",
        "Reversal for the failed transaction never came. Support keeps asking me to wait.",
    ],
    "onboarding_bank_linking": [
        "OTP never arrives when I try to link my {bank} bank account. Tried 5 times.",
        "Cannot register. SIM binding keeps failing even though my number is active.",
        "The sign up flow is a maze. Bank not listed and no way to continue after that.",
        "Stuck at bank linking since the last update. Verification SMS is not coming.",
        "Unable to complete setup, it says verify my number and then loops back to the start.",
        "Tried to add bank account three times, always an error with no explanation.",
        "OTP bhejta hi nahi hai, bank account link nahi ho raha. Update ke baad se problem hai.",
        "New phone, could not register, SIM verification fails every time.",
    ],
    "upi_pin_auth": [
        "UPI PIN reset does not work. It rejects my debit card details every time.",
        "Forgot PIN and the reset flow keeps failing. Locked out of payments.",
        "Says incorrect PIN even though I just set it. Cannot pay anyone.",
        "Biometric login stopped working after the update, fingerprint is not recognised.",
        "UPI PIN change option is hidden and the set pin step errors out for {bank}.",
        "PIN reset ke time debit card details wrong bata raha hai, sahi hain.",
    ],
    "kyc_limits": [
        "My KYC was rejected twice with no reason given. Now my account is blocked.",
        "Hit the daily limit and the app does not explain when it resets. Needed to pay rent.",
        "Account suspended suddenly, asked to upload documents again though I already did.",
        "Aadhaar verification fails every time, no idea what is wrong with the document.",
        "Per day limit is confusing, it changed without any notice.",
        "KYC document rejected, and support gives a generic reply.",
    ],
    "rewards_cashback": [
        "Cashback of Rs {amt} not credited even though the offer said instant.",
        "Scratch card shows better luck next time every single time. Rewards feel fake.",
        "Offer terms are unclear and the reward disappeared after I paid.",
        "Points not added for the last {nth} transactions despite the offer.",
        "Cashback nahi mila, offer sirf dikhane ke liye hai kya?",
        "Coupon expired before I could use it and there was no reminder.",
    ],
    "app_performance": [
        "App crashes every time I open the payment screen since the last update.",
        "Very slow to load, sometimes freezes for a minute during checkout.",
        "The app keeps closing on my phone after version {ver}. Unusable.",
        "Blank screen on launch. Have to force close and restart it three times.",
        "Laggy and hangs when I scroll history. Battery drain is also bad.",
        "App band ho jata hai payment ke time. Bahut slow hai.",
        "Loading forever on the home screen, crash after crash this week.",
    ],
    "customer_support": [
        "Customer care gave a generic copy paste reply and closed my ticket without fixing anything.",
        "No response from the support team for {days} days. Raised a complaint twice.",
        "The chatbot keeps looping and there is no way to reach a human agent.",
        "Helpline never picks up. Escalated twice and nobody replies.",
        "Support nahi milta, ticket close kar dete hain bina solve kiye.",
        "Raised a complaint about a failed payment, still no reply from anyone.",
    ],
    "fraud_scam": [
        "Got a suspicious collect request from an unknown number. It looked like a scam.",
        "Unauthorised debit of Rs {amt} from my account and the app gave no way to report fraud quickly.",
        "Fake merchant took my money and I cannot find how to report it in the app.",
        "Phishing messages claim to be from the app and ask for my UPI PIN. Please add warnings.",
        "Someone cheated me with a fake refund link, money gone, no dispute option.",
        "Spam calls after I used the app, I think my data was leaked.",
    ],
    "merchant_qr": [
        "QR scan fails in low light, shop owner waits and customers pile up.",
        "Merchant settlement is delayed by {days} days, cash flow is suffering for my dukaan.",
        "Soundbox did not announce the payment so I could not confirm it was received.",
        "Scanner cannot read a slightly damaged QR code, have to type the UPI ID.",
        "Customer paid but the merchant app does not show it. Settlement status is unclear.",
        "Vendor payments through QR keep showing failed even though the customer's bank debited.",
    ],
    "ux_features": [
        "Too many ads on the home screen, it is cluttered and I cannot find send money.",
        "Please add a bill split feature. We split rent every month and do it manually.",
        "Hard to find old transactions, history search is missing.",
        "Notification overload, every offer pings me three times a day.",
        "Navigation is confusing. Too many steps to just pay a friend.",
        "Would love dark mode and a simpler interface.",
    ],
}

# Complaints that contain NO obvious keyword: rule-based extraction should miss these.
HARD = [
    ("payment_failure", "Wah, great service. My money left my account and the other person has seen nothing for three days."),
    ("payment_failure", "Sent my landlord the rent. Bank says gone, he says never arrived. Somebody is lying and it is not me."),
    ("onboarding_bank_linking", "Installed it on my new phone and honestly could not get past the first few screens."),
    ("customer_support", "I explained my problem four times to four different people and each time they asked me to start over."),
    ("refund_delay", "They promised to put the money back in 24 hours. It is now the second week."),
    ("app_performance", "Opening the app feels like waiting for a bus. Every single time."),
    ("fraud_scam", "A stranger knew my transaction details and asked me to share a code. Something is not right with this app."),
    ("ux_features", "I still cannot figure out where my passbook lives. Why is everything hidden?"),
]

PRAISE = [
    "Best UPI app, super fast payments and a clean interface.",
    "Works great. I use it every day for groceries and bills.",
    "Love the app, smooth experience and instant payments.",
    "Good app for daily use. Recharge and bill payments are easy.",
    "Excellent. Never faced any issue so far.",
    "Simple and reliable. Highly recommend to everyone.",
    "Rewards are decent and payments are quick.",
    "The new design is clean. Happy with the update.",
]
# Praise that contains a pain keyword in a negated sense: rules with a negation guard should skip, some will not.
NEGATED_PRAISE = [
    "No failed transactions for months, payments are instant.",
    "Never had a crash and the support team replied within an hour.",
    "Cashback came instantly and there was no pending amount at all.",
    "Zero issues with OTP, linking my bank took a minute.",
    "Refund came back in two hours, great service.",
]
SPAM = ["nice", "good app", "ok", "super", "worst", "bad", "fine", "wow", "useless", "thanks"]

CHURN_TAILS = [
    "I am switching to GPay if this continues.",
    "Uninstalling this app today.",
    "Last time I use this app.",
    "Moving to Paytm after this.",
    "Going to delete the app and use my bank app instead.",
    "App chhod raha hoon ab.",
    "Looking for an alternative UPI app now.",
    "Won't use this again.",
]
HARD_CHURN_TAILS = [
    "Done with this. Taking my money elsewhere.",
    "I will go back to the other app I used earlier.",
]
FALSE_CHURN = [
    "I thought about uninstalling but support finally fixed it, so staying for now.",
    "My friend is switching to GPay but I am fine with this app.",
]

SOURCES = [("play_store", 0.45), ("app_store", 0.12), ("reddit", 0.11), ("ticket", 0.15),
           ("sales_call", 0.07), ("interview", 0.04), ("whatsapp", 0.06)]

# base category mix
BASE_W = {
    "payment_failure": 24, "refund_delay": 9, "onboarding_bank_linking": 9, "upi_pin_auth": 7, "kyc_limits": 6,
    "rewards_cashback": 10, "app_performance": 9, "customer_support": 10, "fraud_scam": 5, "merchant_qr": 6, "ux_features": 6,
}


def weighted(rng, pairs):
    r = rng.random() * sum(w for _, w in pairs)
    for k, w in pairs:
        r -= w
        if r <= 0:
            return k
    return pairs[-1][0]


def version_for(d):
    if d < datetime(2026, 9, 25):
        return "9.2"
    if d < datetime(2026, 10, 15):
        return "9.3"
    return "9.4"


def fill(rng, tpl, ver):
    return tpl.format(amt=rng.choice(AMOUNTS), bank=rng.choice(BANKS), days=rng.choice(DAYS),
                      nth=rng.choice(["second", "third", "fourth"]), ver=ver)


def pii_tail(rng):
    kind = rng.choice(["phone", "upi", "acct", "email"])
    if kind == "phone":
        return f" My number is {rng.choice('6789')}{rng.randint(10**8, 10**9 - 1)}, please call."
    if kind == "upi":
        return f" My UPI ID is user{rng.randint(100, 999)}@{rng.choice(['okaxis', 'ybl', 'oksbi', 'paytm'])}."
    if kind == "acct":
        return f" Account ending {rng.randint(10**11, 10**12 - 1)} was debited."
    return f" Reach me at person{rng.randint(10, 99)}@gmail.com."


def wrap(rng, source, text, i):
    if source == "ticket":
        return f"TKT-{rng.randint(10000, 99999)}: {text}"
    if source == "sales_call":
        return f"Call notes, customer reports - {text}"
    if source == "interview":
        return f"Participant P{rng.randint(1, 12)}: {text}"
    if source == "whatsapp":
        return f"[OCR from WhatsApp screenshot] {text}"
    if source == "reddit":
        return f"r/IndiaInvestments thread: {text}"
    return text


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=420)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    rng = random.Random(args.seed)

    start = datetime(2026, 9, 4)
    end = datetime(2026, 10, 30)
    span = (end - start).days

    rows, gold = [], {}
    for i in range(args.n):
        day = start + timedelta(days=rng.randint(0, span), hours=rng.randint(7, 23), minutes=rng.randint(0, 59))
        recent = day >= datetime(2026, 10, 16)
        ver = version_for(day)
        source = weighted(rng, SOURCES)
        w = dict(BASE_W)
        if recent:  # v9.4 regression: bank linking spikes, crashes and refunds creep up
            w["onboarding_bank_linking"] *= 9.0
            w["app_performance"] *= 1.8
            w["refund_delay"] *= 1.4
        kind = rng.random()
        cats, churn, text = [], False, ""

        if kind < 0.045:  # spam
            text = rng.choice(SPAM)
        elif kind < 0.20:  # praise
            text = rng.choice(PRAISE)
            if rng.random() < 0.3:
                text = rng.choice(NEGATED_PRAISE)
            if rng.random() < 0.05:
                text += " " + rng.choice(FALSE_CHURN)
        elif kind < 0.25:  # hard complaint, no keywords
            c, text = rng.choice(HARD)
            cats = [c]
            if rng.random() < 0.3:
                text += " " + rng.choice(HARD_CHURN_TAILS)
                churn = True
        else:
            c1 = weighted(rng, list(w.items()))
            cats = [c1]
            text = fill(rng, rng.choice(T[c1]), ver)
            if rng.random() < 0.12:  # multi-issue
                c2 = weighted(rng, [(k, v) for k, v in w.items() if k != c1])
                cats.append(c2)
                t2 = fill(rng, rng.choice(T[c2]), ver)
                text += " Also, " + t2[0].lower() + t2[1:]
            if rng.random() < 0.14:
                text += " " + rng.choice(CHURN_TAILS)
                churn = True
            elif rng.random() < 0.02:
                text += " " + rng.choice(HARD_CHURN_TAILS)
                churn = True

        if rng.random() < 0.12:
            text += pii_tail(rng)

        text = wrap(rng, source, text, i)
        rid = f"{source[:2]}_{day.strftime('%Y%m%d')}_{i:04d}"
        rating = ""
        if source in ("play_store", "app_store"):
            rating = rng.choice([1, 1, 2, 2, 3]) if cats else rng.choice([4, 5, 5, 5])
            if text in ("worst", "bad", "useless"):
                rating = 1
        rows.append({
            "id": rid, "source": source, "timestamp": day.strftime("%Y-%m-%d %H:%M:%S"), "text": text,
            "rating": rating, "app_version": ver if source in ("play_store", "app_store", "reddit") else "",
            "user_segment": rng.choice(["new_user", "regular", "power_user", "merchant" if source == "sales_call" else "regular"]),
            "is_synthetic": "true",
        })
        gold[rid] = {"categories": cats, "churn": churn}

    rows.sort(key=lambda r: r["timestamp"])
    (ROOT / "data").mkdir(exist_ok=True)
    with open(ROOT / "data" / "feedback.csv", "w", newline="", encoding="utf-8") as f:
        wri = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        wri.writeheader()
        wri.writerows(rows)
    (ROOT / "data" / "golden.json").write_text(json.dumps(gold, indent=1), encoding="utf-8")
    n_pain = sum(1 for g in gold.values() if g["categories"])
    n_churn = sum(1 for g in gold.values() if g["churn"])
    print(f"wrote {len(rows)} records ({n_pain} with pain points, {n_churn} churn) to data/feedback.csv + data/golden.json")


if __name__ == "__main__":
    main()
