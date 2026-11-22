"""A small store that looks like work, for the tests that draw (tests/test_page.py, tests/test_kitty_icon.py) and for the
pictures in screenshots/. build(root) fills an empty folder and returns what the tests look for.

Every text is made up. The words that begin with ZQ are in bodies only (never in a first line, so never in a title): a test
that finds one on the screen at level 2 has found a leak.

    python tests/demo_store.py DIR     build one by hand (then: python -m breadcrumbs --home DIR serve --open)
"""
import json
import os
import sys
import time

from breadcrumbs.crumbs import Crumb, get_crumb, put_crumb
from breadcrumbs.history import Refs, _ref_name
from breadcrumbs.index import Index
from breadcrumbs.ingest import put_rows, put_tree
from breadcrumbs.lines import put_text, split_lines
from breadcrumbs.store import Store

T0 = 1795000000  # 2026-11-18, a Wednesday (UTC); the sessions lie on four days from it
SECRET = "ZQSECRET"       # in the private bodies
SHARED = "ZQSHARED"       # in the public bodies
QUERY = "ZQQUERY"         # in the query of a recall

HARNESSES = ("hermes", "claude-code", "codex", "opencode", "pi", "grok")


def _call(i, name, **args):
    return {"id": "call-%d" % i, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


def _chat(topic, n, secret, ts, tools=True):
    """The rows of one chat about a topic: n user turns, each with a step that calls tools and an answer."""
    rows, k = [], 0
    for t in range(n):
        rows.append({"role": "user", "ts": ts, "content": "%s question %d\nthe long form of it holds %s and the word %s again\n" % (topic, t + 1, secret, topic)})
        ts += 20
        if tools:
            calls = [_call(k, "read_file", path="notes/%s.md" % topic), _call(k + 1, "terminal", command="grep -n %s notes/*.md" % topic)]
            rows.append({"role": "assistant", "ts": ts, "content": "looking at the %s notes\n%s is in the plan" % (topic, secret), "tool_calls": calls})
            rows.append({"role": "tool", "ts": ts + 5, "tool_call_id": "call-%d" % k, "tool_name": "read_file",
                         "content": "# %s\nline one %s\nline two\nline three\n" % (topic, secret)})
            rows.append({"role": "tool", "ts": ts + 9, "tool_call_id": "call-%d" % (k + 1), "tool_name": "terminal",
                         "content": json.dumps({"output": "notes/%s.md:2:line one %s" % (topic, secret), "exit_code": 0})})
            k += 2
            ts += 30
            if t == 1:
                edit = [_call(k, "patch", path="notes/%s.md" % topic, old_string="line two", new_string="line 2 %s" % secret),
                        _call(k + 1, "delegate_task", tasks=[{"goal": "check the %s numbers %s" % (topic, secret)}])]
                rows.append({"role": "assistant", "ts": ts, "content": "", "tool_calls": edit})
                rows.append({"role": "tool", "ts": ts + 4, "tool_call_id": "call-%d" % k, "tool_name": "patch",
                             "content": json.dumps({"success": True, "diff": "-line two\n+line 2 %s" % secret})})
                rows.append({"role": "tool", "ts": ts + 8, "tool_call_id": "call-%d" % (k + 1), "tool_name": "delegate_task",
                             "content": json.dumps({"summary": "the numbers hold %s" % secret})})
                k += 2
                ts += 20
        rows.append({"role": "assistant", "ts": ts, "content": "%s answer %d\nthe %s plan is settled: %s, and the %s stays\n" % (topic, t + 1, topic, secret, topic)})
        ts += 40
    return rows, ts


def build(root, harness_logs=True):
    """Fill the empty folder root with the store. Returns {"sids": [...], "sess": {sid: crumb id}, "first": {sid: first turn
    id}, "rcpt": id, "public": sid, "private": sid, "words": the words of the bodies}."""
    os.makedirs(root, exist_ok=True)
    store, index, refs = Store(os.path.join(root, "store")), Index(os.path.join(root, "index.db")), Refs(os.path.join(root, "refs"))
    plan = [  # sid, topic, user turns, public, day, hour, tools
        ("local-kettle-0001", "kettle", 3, False, 0, 9, True),
        ("local-garden-0002", "garden", 2, False, 0, 14, True),
        ("local-teanotes-0003", "kettle", 2, True, 1, 10, False),
        ("local-deploy-0004", "deploy", 3, False, 1, 16, True),
        ("local-ledger-0005", "ledger", 2, False, 2, 8, True),
        ("local-garden-0006", "garden", 1, False, 2, 20, False),
        ("local-deploy-0007", "deploy", 2, False, 3, 11, True),
        ("local-recipes-0008", "recipes", 2, True, 3, 15, False),
    ]
    out = {"sids": [], "sess": {}, "first": {}, "t0": {}, "words": (SECRET, SHARED, QUERY)}
    for sid, topic, n, public, day, hour, tools in plan:
        ts = T0 + day * 86400 + hour * 3600
        rows, end = _chat(topic, n, SHARED if public else SECRET, ts, tools)
        turns, _ = put_rows(store, index, rows, sid)
        if public:  # a chat that its owner published: the crumb and every turn of it
            again = []
            for cid in turns:
                c = get_crumb(store, cid)
                c.public = True
                again.append(put_crumb(store, c))
            turns = again
        sess = put_tree(store, "sess", get_crumb(store, turns[0]).title, turns, public=public, meta={"sess": sid, "ts": ts})
        refs.set(_ref_name("sess-", sid), sess)
        os.utime(os.path.join(root, "refs", _ref_name("sess-", sid)), (end, end))  # graph() lists the chat that moved last first
        out["sids"].append(sid)
        out["sess"][sid], out["first"][sid], out["t0"][sid] = sess, turns[0], ts
    out["private"], out["public"] = plan[0][0], plan[2][0]
    # one recall in the first chat: its query, and what the model saw of the first turn
    sid, first = out["private"], out["first"][plan[0][0]]
    seen = ("recalled for the model: %s\n" % SECRET).encode("utf-8")
    ctx = put_text(store, seen)
    out["rcpt"] = put_crumb(store, Crumb("rcpt", "recall", kids=[first], ranges=[[ctx, 0, len(split_lines(seen))]], meta={
        "sess": sid, "ev": "recall", "ts": out["t0"][sid] + 100, "turn": 1, "items": [first], "lv": [0.5], "b": [64], "bytes": 64,
        "budget": 4000, "ovf": False, "rate": 0.5, "band": [0.1, 0.9], "prev": "", "q": "where is the %s kettle note" % QUERY, "ctx": ctx}))
    refs.set(_ref_name("rcpt-", sid), out["rcpt"])
    index.commit()
    index.close()
    store.close()
    if harness_logs:
        logs(root, out)
    with open(os.path.join(root, "trail.json"), "w", encoding="utf-8") as f:
        json.dump({"session": sid, "dropped": 0, "stack": [{"id": out["sess"][sid], "level": 0.25, "label": "sess kettle question plan"}],
                   "line": "sess kettle question plan"}, f)
    return out


def logs(root, out):
    """ace.log and usage.log of the first chat: twelve requests, a fold at the eighth, the cache read and written."""
    sid, t = out["private"], out["t0"][out["private"]]
    ace, usage, sent, folds = [], [], 30000, 0
    for i in range(12):
        ts = t + 60 + i * 45
        raw = 30000 + i * 9000
        act = "fold" if i == 7 else "keep" if i % 3 else "pass"
        if act == "fold":
            sent, folds = 42000, folds + 1
        else:
            sent = min(raw, sent + 9000)
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(ts))
        ace.append("%s pid=4242 sess=%s act=%s in=%d out=%d last=%d base=12000 tpb=0.5 r=0.5 folds=%d layer=800 target=90000 "
                   "gain=0.9 lay=%d gear=- think=0 calls=2 work=0 room=0\n" % (stamp, sid[:24], act, raw, sent, max(0, sent - 900), folds,
                                                                              max(0, raw - sent)))
        cr, cw = (0, sent) if i in (0, 8) else (sent - 9000, 9000)
        turn = "%s:%s:t%d" % (sid, sid, i)
        usage.append("%s sess=%s turn=%s req=%s:api:1 n=1 task=main model=claude-haiku-5-5 resp=claude-haiku-5-5 prov=anthropic in=40 "
                     "cr=%d cw=%d out=600 rsn=0 prompt=%d est=0 msgs=%d tools=6 dur=2.500 fin=stop\n"
                     % (time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(ts + 2)), sid, turn, turn, cr, cw, 40 + cr + cw, 4 + 2 * i))
    with open(os.path.join(root, "ace.log"), "w", encoding="utf-8") as f:
        f.write("".join(ace))
    with open(os.path.join(root, "usage.log"), "w", encoding="utf-8") as f:
        f.write("".join(usage))


if __name__ == "__main__":
    print(json.dumps(build(sys.argv[1]), indent=1))
