"""Local Vector-Search & Semantic Contract Clause RAG.

Provides deterministic clause retrieval across supplier contracts
with zero cloud dependencies and prompt injection defense.
"""

from __future__ import annotations

import math
import re
from typing import Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from src.models import ContractClause, Vendor
from src.schemas import ContractClauseResponse
from src.security import sanitize_untrusted_text


STOPWORDS = frozenset(
    "a an and are as at be by for from has have if in into is it its of on or per the "
    "to up upon what when which with within will".split()
)


def _tokenize(text: str) -> List[str]:
    """Extract lowercased word tokens, dropping stopwords."""
    return [t for t in re.findall(r"\b[a-zA-Z0-9_-]{2,}\b", text.lower()) if t not in STOPWORDS]


def _compute_vector(
    tokens: List[str], vocabulary: Dict[str, int], idf: Optional[Dict[str, float]] = None
) -> List[float]:
    """Compute an L2-normalized TF-IDF vector (plain TF when no IDF table is given)."""
    vec = [0.0] * len(vocabulary)
    if not tokens:
        return vec

    for t in tokens:
        if t in vocabulary:
            vec[vocabulary[t]] += idf.get(t, 1.0) if idf else 1.0

    # L2 normalize
    norm = math.sqrt(sum(v * v for v in vec))
    if norm > 0:
        vec = [v / norm for v in vec]
    return vec


def _cosine_similarity(v1: List[float], v2: List[float]) -> float:
    """Compute cosine similarity between two unit vectors."""
    return sum(a * b for a, b in zip(v1, v2))


class LocalContractRAG:
    """Zero-dependency deterministic vector search over contract clauses."""

    @staticmethod
    def search_clauses(
        db: Session,
        tenant_id: str,
        query: str,
        vendor_id: Optional[int] = None,
        top_k: int = 3,
    ) -> List[ContractClauseResponse]:
        """Retrieve most relevant contractual clauses for a natural language query."""
        # Sanitize query against prompt injection overrides
        safe_query = sanitize_untrusted_text(query)
        query_tokens = _tokenize(safe_query)
        if not query_tokens:
            return []

        # Retrieve clauses scoped strictly to tenant
        q = db.query(ContractClause, Vendor.name).join(
            Vendor, ContractClause.vendor_id == Vendor.id
        ).filter(ContractClause.tenant_id == tenant_id)

        if vendor_id:
            q = q.filter(ContractClause.vendor_id == vendor_id)

        results = q.all()
        if not results:
            return []

        # Build local vocabulary and inverse document frequencies across the corpus
        clause_token_lists = [
            _tokenize(f"{clause.clause_category.replace('_', ' ')} {clause.title} {clause.content}")
            for clause, _ in results
        ]
        vocab: Dict[str, int] = {}
        doc_freq: Dict[str, int] = {}
        for tokens in clause_token_lists:
            for token in set(tokens):
                doc_freq[token] = doc_freq.get(token, 0) + 1
            for token in tokens:
                vocab.setdefault(token, len(vocab))
        for token in query_tokens:
            vocab.setdefault(token, len(vocab))

        n_docs = len(clause_token_lists)
        idf = {t: math.log((n_docs + 1) / (doc_freq.get(t, 0) + 1)) + 1.0 for t in vocab}

        query_vec = _compute_vector(query_tokens, vocab, idf)

        scored_clauses: List[Tuple[float, ContractClause, str]] = []
        for (clause, vendor_name), clause_tokens in zip(results, clause_token_lists):
            clause_vec = _compute_vector(clause_tokens, vocab, idf)
            sim = _cosine_similarity(query_vec, clause_vec)
            scored_clauses.append((sim, clause, vendor_name))

        # Sort descending by similarity score
        scored_clauses.sort(key=lambda x: x[0], reverse=True)

        responses: List[ContractClauseResponse] = []
        for score, clause, vendor_name in scored_clauses[:top_k]:
            responses.append(
                ContractClauseResponse(
                    id=clause.id,
                    vendor_id=clause.vendor_id,
                    vendor_name=vendor_name,
                    clause_category=clause.clause_category,
                    title=clause.title,
                    content=clause.content,
                    similarity_score=round(max(0.0, score), 4),
                )
            )

        return responses
