from collections import Counter, defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo
import json
import time
from .policy import rank_opportunity

METRICS=["impressions","likes","replies","meaningful_replies","reposts","profile_visits","clicks","qualified_clicks"]


def report(db,settings,now=None):
    now=now or time.time()
    with db.connect() as c:
        rows=c.execute("SELECT d.id,d.body,x.at,x.mode,x.post_id FROM drafts d JOIN deliveries x ON x.draft_id=d.id WHERE x.mode=? ORDER BY x.at DESC",(settings.mode,)).fetchall()
        records=[]
        for row in rows:
            latest=c.execute("SELECT body,captured_at FROM metrics WHERE draft_id=? ORDER BY captured_at DESC,id DESC LIMIT 1",(row['id'],)).fetchone()
            m=json.loads(latest[0]) if latest else {k:None for k in METRICS}
            records.append({"id":row['id'],"body":json.loads(row['body']),"posted_at":row['at'],"age_hours":round((now-row['at'])/3600,1),
                            "measurement_age_hours":round((latest['captured_at']-row['at'])/3600,1) if latest else None,
                            "metrics":m,"mode":row['mode'],"post_id":row['post_id']})
        profile=json.loads(c.execute("SELECT body FROM profile WHERE id=1").fetchone()[0])
        conversations=[json.loads(r[0]) for r in c.execute("SELECT body FROM opportunities ORDER BY seen_at DESC")]
        questions=[o['text'] for o in conversations if '?' in o['text'] and rank_opportunity(c,o,profile,now)['appropriate'] and (settings.mode=='demo' or not o['fictional'])]
        question_counts=Counter(questions)
        bodies=[json.loads(r[0]) for r in c.execute("SELECT body FROM drafts WHERE created_at>=? AND status!='discarded'",(now-7*86400,))]
        if settings.mode=='manual':
            bodies=[b for b in bodies if not b['fictional']]
    # Use snapshots captured 24–48 hours after posting for comparable exposure, not the post's age now.
    week=[r for r in records if now-7*86400<=r['posted_at']<=now]
    comparable=[r for r in week if r['measurement_age_hours'] is not None and 24<=r['measurement_age_hours']<=48]
    comparisons={}
    for dimension in ["hook","topic","kind","cta","posting_hour"]:
        groups=defaultdict(list)
        for r in comparable:
            value=str(datetime.fromtimestamp(r['posted_at'],ZoneInfo(settings.timezone)).hour) if dimension=='posting_hour' else r['body'][dimension]
            groups[value].append(r)
        results=[]
        for value,items in groups.items():
            metrics={}
            for key in METRICS:
                available=[r['metrics'].get(key) for r in items if r['metrics'].get(key) is not None]
                metrics[key]={"total":sum(available) if available else None,"available_posts":len(available)}
            # Pairwise availability prevents missing numerator observations being treated as zeros.
            rates={}
            for key in ['meaningful_replies','profile_visits','qualified_clicks']:
                pairs=[r['metrics'] for r in items if r['metrics'].get(key) is not None and r['metrics'].get('impressions') is not None and r['metrics']['impressions']>0]
                impressions=sum(m['impressions'] for m in pairs)
                rates[key]={"per_1000_impressions":round(sum(m[key] for m in pairs)*1000/impressions,2) if impressions else None,"paired_posts":len(pairs)}
            results.append({"value":value,"posts":len(items),"metrics":metrics,"rates":rates})
        comparisons[dimension]=results
    themes=Counter(b['topic'] for b in bodies)
    repetitive=[{"topic":topic,"drafts":count} for topic,count in themes.most_common() if count>=3]
    totals={key:{"total":sum(r['metrics'][key] for r in week if r['metrics'].get(key) is not None) if any(r['metrics'].get(key) is not None for r in week) else None,
                 "available_posts":sum(r['metrics'].get(key) is not None for r in week)} for key in METRICS}
    meaningful=[r for r in comparable if r['metrics'].get('meaningful_replies') is not None]
    successes=sorted(meaningful,key=lambda r:r['metrics']['meaningful_replies'],reverse=True)[:3]
    low=sorted(meaningful,key=lambda r:r['metrics']['meaningful_replies'])[:3]
    return {"period_days":7,"timezone":settings.timezone,"posts":records,"totals":totals,"comparisons":comparisons,
            "weekly":{
                "sample_size":len(week),"comparable_posts":len(comparable),
                "assessment":"Descriptive observations only; no winning strategy or causal attribution. " + ("Insufficient sample: fewer than 20 comparable posts." if len(comparable)<20 else "Review group sizes, metric coverage, and confounders before designing a controlled experiment."),
                "measurement_window":"Only metric snapshots captured 24–48 hours after posting enter comparisons. Missing values remain unavailable.",
                "meaningful_engagement":[{"draft_id":r['id'],"topic":r['body']['topic'],"meaningful_replies":r['metrics']['meaningful_replies']} for r in successes],
                "underperformed":[{"draft_id":r['id'],"topic":r['body']['topic'],"meaningful_replies":r['metrics']['meaningful_replies']} for r in low],
                "repetitive_themes":repetitive,"community_questions":[q for q,_ in question_counts.most_common(8)],
                "question_frequencies":[{"question":q,"occurrences":n} for q,n in question_counts.most_common(8)],
                "experiments":["Compare a concrete-example hook with a question hook on the same topic; keep frequency unchanged.","Try one less-used theme and manually classify whether replies contain substantive questions.","Use tagged links and record qualified clicks only when you have an explicit qualification rule."],
                "caveat":"Public replies are not automatically meaningful; qualified clicks and profile visits may be unavailable. Demo results are fictional. No claims about token purchases or price effects."}}
