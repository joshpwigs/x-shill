import time
from .schemas import Profile, Opportunity


def profile(now=None):
    now = now or time.time()
    return Profile(
        name="Moon Garden · fictional demo", ticker="$SPROUT", chain="Fictional test chain",
        account="MoonGardenDemo", fictional=True,
        official_links=["https://example.com/moon-garden"],
        audience="Curious builders who want readable explanations, not price predictions.",
        slang=["touch grass", "garden mode"], jokes=["Our roadmap has actual roots."],
        competitors=["Fictional comparison project: Cloud Cactus"],
        voice="Dry, self-aware humor. Ask useful questions. No price promises.",
        good_examples=["A useful roadmap answers what happens next, and what might go wrong."],
        bad_examples=["100x guaranteed. Buy now."],
        disclosure="Fictional demo · project team",
        forbidden_claims=["guaranteed returns", "risk-free", "official Coinbase partner"],
        keywords=["community", "roadmap", "token mechanics", "onboarding"],
        monitored_accounts=["GardenLearner"],
        facts=[
            {"id":"demo-guide","text":"The fictional garden guide explains token mechanics.","classification":"verified","source":"https://example.com/fictional-guide","verified_at":now,"stale_after_days":30},
            {"id":"demo-opinion","text":"Readable documentation beats a wall of jargon.","classification":"opinion","source":"","stale_after_days":30},
            {"id":"demo-rumor","text":"A fictional exchange listing is rumored.","classification":"unverified","source":"","stale_after_days":7},
            {"id":"demo-stale","text":"The demo workshop launched last month.","classification":"verified","source":"https://example.com/old-workshop","verified_at":now-60*86400,"stale_after_days":7}
        ]).model_dump()


def opportunities(now=None):
    now = now or time.time()
    samples = [
        ("demo-garden", "GardenLearner", "What makes a token mechanics explainer actually readable? Trying to help our community newcomers.", True, 12),
        ("demo-roadmap", "RoadmapReader", "Every roadmap says 'community first'. What should a useful progress update include?", False, 7),
        ("demo-personal", "QuietBuilder", "Our community is grieving after a tragedy. Please give the family space.", False, 3),
        ("demo-unrelated", "BreadScientist", "How long should sourdough rest in the fridge?", False, 8),
    ]
    return [Opportunity(id=i,author=a,text=t,url=f"https://x.com/{a}/status/{10000+n}",
                        posted_at=now-(n+1)*1800,thread_id=i,context="Fictional demo data; this is not a real X post.",
                        inbound=inbound,replies=r,likes=r*2,fictional=True).model_dump()
            for n,(i,a,t,inbound,r) in enumerate(samples)]
