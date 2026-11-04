"""Stage 2 + 3: group pain points into themes, name them, pick verified representative quotes.

Two-level design (what a PM wants to read: stable top-level themes, with sub-themes where volume justifies it):
  level 1  taxonomy category from extraction (rules or LLM)
  level 2  within each category, TF-IDF + average-linkage agglomerative clustering splits the quotes into
           up to MAX_SUB sub-themes; tiny sub-themes are merged into their nearest sibling.
Categories with fewer than MIN_CLUSTER pain points go to a long-tail bucket.

Swap TF-IDF for embeddings by changing `_vectorize` only. Naming here is term-based (offline);
an LLM naming pass is the obvious upgrade and slots in at `_name`.
"""
from __future__ import annotations

from collections import Counter

import numpy as np
from sklearn.cluster import AgglomerativeClustering
from sklearn.feature_extraction import text as sktext
from sklearn.feature_extraction.text import TfidfVectorizer

from .taxonomy import CATEGORY_LABELS

MIN_CLUSTER = 4
MAX_SUB = 3
SUB_EVERY = 14  # one extra sub-theme per ~14 pain points

_EXTRA_STOP = {"rs", "bank", "app", "payment", "payments", "just", "get", "got", "still", "even", "every", "time",
               "also", "hai", "nahi", "kab", "ka", "ki", "ke", "se", "mera", "mere", "ho", "raha", "bhi", "pe",
               "tkt", "customer", "reports", "call", "notes", "participant", "ocr", "whatsapp", "screenshot",
               "thread", "indiainvestments", "days", "day", "twice", "times", "month", "new"}


def _vectorize(texts):
    stop = list(sktext.ENGLISH_STOP_WORDS | _EXTRA_STOP)
    vec = TfidfVectorizer(ngram_range=(1, 2), stop_words=stop, sublinear_tf=True, min_df=2, lowercase=True)
    try:
        X = vec.fit_transform(texts)
    except ValueError:  # vocabulary empty after pruning
        vec = TfidfVectorizer(ngram_range=(1, 1), sublinear_tf=True, lowercase=True)
        X = vec.fit_transform(texts)
    return X.toarray(), vec


def _top_terms(centroid, others_mean, terms, n=2):
    diff = centroid - others_mean
    order = np.argsort(diff)[::-1]
    picked = []
    for i in order:
        if diff[i] <= 0:
            break
        t = terms[i]
        if any(t in p or p in t for p in picked):
            continue
        picked.append(t)
        if len(picked) == n:
            break
    return picked


def _name(label, sub_terms, multi):
    return f"{label} · {', '.join(sub_terms)}" if (multi and sub_terms) else label


def cluster_pain_points(pps: list[dict], min_cluster: int | None = None) -> dict:
    """pps: [{pp_id, record_id, category, quote, source}] -> {"assign": {pp_id: cluster_id}, "meta": {cluster_id: {...}}}

    min_cluster defaults to 2 for small datasets (under 100 pain points) so real categories are not buried in the
    long tail, and MIN_CLUSTER otherwise."""
    min_c = min_cluster if min_cluster is not None else (2 if len(pps) < 100 else MIN_CLUSTER)
    by_cat: dict[str, list[int]] = {}
    for i, p in enumerate(pps):
        by_cat.setdefault(p["category"], []).append(i)

    assign, meta, next_id = {}, {}, 0
    long_tail = []
    for cat, idxs in sorted(by_cat.items(), key=lambda kv: -len(kv[1])):
        if len(idxs) < min_c:
            long_tail += idxs
            continue
        texts = [pps[i]["quote"] for i in idxs]
        dense, vec = _vectorize(texts)
        k = max(1, min(MAX_SUB, len(idxs) // SUB_EVERY))
        if k > 1:
            labels = AgglomerativeClustering(n_clusters=k, metric="cosine", linkage="average").fit_predict(dense)
            sizes = Counter(labels)
            small = [l for l, n in sizes.items() if n < min_c]
            if small and len(sizes) > 1:  # merge tiny sub-themes into the largest sibling
                big = max((l for l in sizes if l not in small), key=lambda l: sizes[l], default=None)
                if big is not None:
                    labels = np.array([big if l in small else l for l in labels])
        else:
            labels = np.zeros(len(idxs), dtype=int)
        terms = np.array(vec.get_feature_names_out())
        multi = len(set(labels)) > 1
        cat_mean = dense.mean(axis=0)
        for sub in sorted(set(labels), key=lambda l: -int((labels == l).sum())):
            members = np.where(labels == sub)[0]
            centroid = dense[members].mean(axis=0)
            others = dense[np.where(labels != sub)[0]]
            others_mean = others.mean(axis=0) if len(others) else cat_mean
            sub_terms = _top_terms(centroid, others_mean, terms) if multi else []
            dist = np.linalg.norm(dense[members] - centroid, axis=1)
            order = members[np.argsort(dist)]
            quotes, seen_text, seen_src = [], set(), set()
            for j in order:  # first pass: spread across sources
                q = pps[idxs[j]]
                if q["quote"] in seen_text or q["source"] in seen_src:
                    continue
                quotes.append(q["pp_id"]); seen_text.add(q["quote"]); seen_src.add(q["source"])
                if len(quotes) == 3:
                    break
            for j in order:
                if len(quotes) == 3:
                    break
                q = pps[idxs[j]]
                if q["quote"] not in seen_text:
                    quotes.append(q["pp_id"]); seen_text.add(q["quote"])
            meta[next_id] = {"name": _name(CATEGORY_LABELS.get(cat, cat), sub_terms, multi), "category": cat,
                             "purity": 1.0, "top_terms": sub_terms, "quote_pp_ids": quotes, "size": int(len(members))}
            for j in members:
                assign[pps[idxs[j]]["pp_id"]] = next_id
            next_id += 1
    if long_tail:
        for i in long_tail:
            assign[pps[i]["pp_id"]] = -1
        pick = [pps[i]["pp_id"] for i in long_tail[:3]]
        meta[-1] = {"name": "Long tail (small themes)", "category": "long_tail", "purity": 1.0, "top_terms": [],
                    "quote_pp_ids": pick, "size": len(long_tail)}
    return {"assign": assign, "meta": meta}
