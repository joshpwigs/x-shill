"""Conservative screening aids human review; it cannot prove a claim is true."""
from difflib import SequenceMatcher
import json
import re
import time
from .db import digest

SENSITIVE = re.compile(r"\b(death|died|funeral|suicide|self[- ]harm|cancer|diagnosed|shooting|earthquake|tragedy|hospitalized|grieving|overdose|assault|rape|abuse|war victims|bombing|terrorist|disaster|flood|evicted|lost my)\b", re.I)
PROMISES = re.compile(r"\b(guaranteed (?:profit|returns?|gains?)|risk[- ]free|can't lose|cannot lose|100x guaranteed|buy now|pump (?:the|this)|insider tip)\b", re.I)
FACTUAL = re.compile(r"\b(partner(?:ship|ed)?|listed|listing|endorse(?:d|ment)?|volume|raised|launched|shipped|audited|APY|\d+(?:\.\d+)?%|million|billion)\b", re.I)


def normalized(text):
    return re.sub(r"[^\w]+", " ", text.casefold()).strip()


def content_hash(body):
    return digest(normalized(body["text"]))


def weighted_length(text):
    # Conservative X-length estimate. Emoji sequences may be overcounted, never truncated.
    without_urls = re.sub(r"https?://\S+", "x" * 23, text)
    return sum(1 if ord(ch) <= 0x10FF or 0x2000 <= ord(ch) <= 0x200D or 0x2010 <= ord(ch) <= 0x201F or 0x2032 <= ord(ch) <= 0x2037 else 2 for ch in without_urls)


def approval_fingerprint(body, profile, version):
    return digest({"body": body, "profile": profile, "profile_version": version})


def fact_status(fact, now):
    if fact["classification"] != "verified":
        return fact["classification"]
    if not fact.get("source") or not fact.get("verified_at"):
        return "missing verification"
    if fact["verified_at"] > now + 60:
        return "future verification date"
    if now - fact["verified_at"] > fact["stale_after_days"] * 86400:
        return "stale"
    return "verified"


def inspect_draft(c, body, profile, draft_id=None, now=None):
    now = now or time.time()
    blockers, warnings, sources = [], [], []
    text = body["text"]
    if PROMISES.search(text):
        blockers.append("Unsupported financial promises or pressure to buy are prohibited.")
    for term in profile.get("forbidden_claims", []):
        if term and term.casefold() in text.casefold():
            blockers.append(f"Project forbids this claim: {term}")
    if SENSITIVE.search(text):
        blockers.append("Sensitive context requires a separate human response; promotion is blocked.")
    if len(re.findall(r"(?<!\w)@[A-Za-z0-9_]+", text)) > 1:
        blockers.append("Bulk mentions are prohibited.")
    if len(re.findall(r"(?<!\w)#\w+", text)) > 2:
        blockers.append("Use at most two relevant hashtags.")
    if profile["disclosure"].casefold() not in text.casefold():
        blockers.append("Include the project's affiliation disclosure in the content.")
    if body["fictional"] != profile["fictional"]:
        blockers.append("Draft/project fictional labels do not match. Regenerate after updating the profile.")
    facts = {f["id"]: f for f in profile["facts"]}
    for fid in body["source_ids"]:
        f = facts.get(fid)
        if not f:
            blockers.append(f"Unknown source ID: {fid}")
            continue
        status = fact_status(f, now)
        sources.append({**f, "status": status})
        if status != "verified":
            blockers.append(f"Source {fid} is {status}; verify it before using it as fact.")
    if FACTUAL.search(text):
        # Require a cited verbatim fact containing each high-risk term. This is intentionally conservative.
        for match in FACTUAL.finditer(text):
            if not any(fact_status(f, now) == "verified" and f["text"].casefold() in text.casefold() and match.group().casefold() in f["text"].casefold() for f in sources):
                blockers.append("Unsupported factual claim: " + match.group() + ". Cite a current verified fact verbatim or remove the claim.")
    if profile.get("contract_address") and profile["contract_address"] in text:
        f = facts.get(profile.get("contract_fact_id"))
        if not f or f["id"] not in body["source_ids"] or fact_status(f, now) != "verified" or profile["contract_address"] not in f["text"]:
            blockers.append("Contract address needs its own current, cited verification source.")
    if body["kind"] in {"reply", "quote"}:
        row = c.execute("SELECT body FROM opportunities WHERE id=?", (body.get("opportunity_id"),)).fetchone()
        if not row:
            blockers.append("Reply/quote must reference an imported conversation.")
        else:
            opp = json.loads(row[0])
            rank = rank_opportunity(c, opp, profile, now)
            if not rank["appropriate"]:
                blockers.append("Conversation is sensitive, unrelated, or already engaged; promotion is blocked.")
            if body["kind"] == "reply" and not opp["inbound"]:
                warnings.append("Unsolicited conversation: only a useful, individually reviewed manual response is allowed.")
        warnings.append("Replies and quotes are manual-export only; they are never automatically sent.")
    stripped = normalized(text.replace(profile["disclosure"], ""))
    for row in c.execute("SELECT id,body,status FROM drafts WHERE id!=? AND status!='discarded'", (draft_id or "",)):
        other = json.loads(row["body"])
        candidate = normalized(other["text"].replace(profile["disclosure"], ""))
        similarity = SequenceMatcher(None, stripped, candidate).ratio()
        if stripped == candidate or similarity >= 0.90:
            blockers.append(f"Near-duplicate of draft {row['id'][:8]} ({similarity:.0%} similar).")
        elif similarity >= 0.73:
            warnings.append(f"Similar wording to {row['id'][:8]}; try a different angle.")
    if c.execute("SELECT 1 FROM deliveries WHERE content_hash=?", (content_hash(body),)).fetchone():
        blockers.append("This content has already been delivered.")
    length = weighted_length(text)
    if length > 280 and body["kind"] != "education":
        blockers.append(f"Conservative weighted length is {length}/280. Shorten before approval.")
    if body["kind"] in {"education", "meme"}:
        warnings.append("Threads and image briefs are manual-export only. Review each segment/asset in X.")
    warnings.append("Automated screening cannot verify truth. Review every claim, source, and the original context.")
    return {"blockers": list(dict.fromkeys(blockers)), "warnings": list(dict.fromkeys(warnings)), "sources": sources,
            "weighted_length": length, "schedule_eligible": body["kind"] in {"post", "update", "opinion", "question"}}


def rank_opportunity(c, opp, profile, now=None):
    now = now or time.time()
    text = (opp["text"] + " " + opp.get("context", "")).casefold()
    keys = [k.casefold() for k in profile["keywords"] if k.strip()]
    hits = [k for k in keys if k in text]
    followed = opp["author"].casefold() in {a.lstrip("@").casefold() for a in profile["monitored_accounts"]}
    prior = False
    for row in c.execute("SELECT d.body FROM drafts d JOIN deliveries x ON x.draft_id=d.id"):
        draft = json.loads(row[0])
        if draft.get("opportunity_id"):
            old = c.execute("SELECT body FROM opportunities WHERE id=?", (draft["opportunity_id"],)).fetchone()
            if old:
                old = json.loads(old[0])
                prior |= old["author"].casefold() == opp["author"].casefold() or old["thread_id"] == opp["thread_id"]
    age_hours = max(0, (now - opp["posted_at"]) / 3600)
    sensitive = bool(SENSITIVE.search(text))
    relevant = bool(hits or followed or opp["inbound"])
    useful = "?" in text or bool(re.search(r"\b(how|why|explain|help|feedback|learn)\b", text))
    # Public counts are proxies, not proof of authenticity.
    discussion = min(10, opp["replies"] * 2)
    score = min(100, 20 * min(2, len(hits)) + 10 * followed + 15 * opp["inbound"] + max(0, 15 - age_hours) + 15 * useful + discussion)
    appropriate = relevant and not sensitive and not prior and age_hours <= 168
    reasons = [f"Keyword match: {', '.join(hits)}" if hits else "No configured keyword match",
               f"{age_hours:.1f} hours old", "Question invites a useful answer" if useful else "Add a concrete contribution before replying",
               "Engagement counts do not establish genuine discussion"]
    if followed: reasons.append("Configured public account")
    if opp["inbound"]: reasons.append("Marked as engaging with our account; verify original context")
    if prior: reasons.append("Already engaged with this author or thread")
    if sensitive: reasons.append("Sensitive/personal context — excluded")
    return {"score": round(score) if appropriate else 0, "appropriate": appropriate, "already_engaged": prior, "reasons": reasons}
