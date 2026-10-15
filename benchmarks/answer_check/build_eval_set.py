"""Build the labelled answer-time citation-support eval set from CAP Colorado opinions.

Source: Caselaw Access Project static export (https://static.case.law/colo/<vol>/cases/*.json),
volumes 190-200 (even), majority opinions only. Download them into a cache dir first
(see README.md). Labels are assigned by construction, then spot-checked by hand:

* supported      - a holding sentence ("we hold/conclude ...") rewritten into third person,
                   paired with the opinion window (2 sentences before/after) that contains it.
* neg            - the same claim with a negation inserted/removed (label 0).
* wrong_holding  - the same claim with the outcome flipped via an antonym swap
                   (affirm<->reverse, did<->did not, valid<->invalid, ...) (label 0).
* wrong_case     - a holding from a different Colorado case, cited against this case's
                   window (label 0): the citation points at the wrong case.

Deterministic: seed 7.  Usage: python build_eval_set.py <cache_dir> <out.jsonl>
"""
from __future__ import annotations
import glob, json, random, re, sys

HOLD = re.compile(r"^(?:(?:Accordingly|Therefore|Thus|Consequently|Hence|For these reasons|For the reasons stated),?\s+)?we (hold|conclude) that (.+)$", re.I)
ANTONYMS = [(" is not ", " is "), (" was not ", " was "), (" did not ", " did "), (" does not ", " does "),
            (" not ", " "), (" affirm", " reverse"), (" reverse", " affirm"), (" erred", " did not err"),
            (" unconstitutional", " constitutional"), (" constitutional", " unconstitutional"),
            (" invalid", " valid"), (" valid", " invalid"), (" improper", " proper"), (" proper", " improper"),
            (" sufficient", " insufficient"), (" insufficient", " sufficient"), (" may ", " may not "),
            (" must ", " need not "), (" applies", " does not apply"), (" lacked", " had"), (" had ", " lacked ")]
NEG = [(" is ", " is not "), (" was ", " was not "), (" are ", " are not "), (" were ", " were not "),
       (" did ", " did not "), (" does ", " does not "), (" can ", " cannot "), (" should ", " should not "),
       (" has ", " has not "), (" have ", " have not ")]
SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"“(])")


def sentences(text: str) -> list[str]:
    out = []
    for para in text.split("\n"):
        out += [s.strip() for s in SPLIT.split(para) if s.strip()]
    return out


def swap(claim: str, table) -> str | None:
    for a, b in table:
        if a in claim and (table is not NEG or a + "not " not in claim):
            return claim.replace(a, b, 1)
    return None


def main(cache: str, out: str) -> None:
    rng = random.Random(7)
    base = []
    for path in sorted(glob.glob(f"{cache}/*.json")):
        case = json.load(open(path))
        ops = [o for o in case["casebody"]["opinions"] if o.get("type") == "majority"]
        if not ops:
            continue
        sents = sentences(ops[0]["text"])
        for i, s in enumerate(sents):
            m = HOLD.match(s)
            if m and 60 <= len(m.group(2)) <= 400:
                verb = "held" if m.group(1).lower() == "hold" else "concluded"
                claim = f"The court {verb} that {m.group(2)}"
                window = " ".join(sents[max(0, i - 2): i + 3])
                base.append({"case": case["name_abbreviation"], "cite": case["citations"][0]["cite"],
                             "cap_id": case["id"], "claim": claim, "source": window})
                break  # one holding per case keeps cases independent
    rng.shuffle(base)
    items = []
    for k, b in enumerate(base):
        items.append({**b, "label": 1, "kind": "supported"})
        r = k % 3
        if r == 0:
            c = swap(b["claim"], NEG) or swap(b["claim"], ANTONYMS)
            kind = "neg"
        elif r == 1:
            c = swap(b["claim"], ANTONYMS) or swap(b["claim"], NEG)
            kind = "wrong_holding"
        else:
            other = base[(k + 1) % len(base)]
            c, kind = other["claim"], "wrong_case"
        if c and c != b["claim"]:
            items.append({**b, "claim": c, "label": 0, "kind": kind})
    for n, it in enumerate(items):
        it["id"] = f"colo-{n:04d}"
    with open(out, "w") as f:
        for it in items:
            f.write(json.dumps(it, ensure_ascii=False) + "\n")
    from collections import Counter
    print(len(items), Counter(i["kind"] for i in items))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
