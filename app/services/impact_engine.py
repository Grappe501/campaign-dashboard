from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from sqlmodel import Session, text


@dataclass(frozen=True)
class ImpactSummary:
    """
    A simple impact summary for a person.

    - downstream_people: count of all recruited descendants (direct + indirect)
    - downstream_voters: count of voter contacts owned by root OR any descendant
    - impact_reach_score: simple additive score (early placeholder)
    """
    person_id: int
    downstream_people: int
    downstream_voters: int
    impact_reach_score: int


def compute_impact(session: Session, person_id: int) -> ImpactSummary:
    """
    Compute downstream people + downstream voters for a given person.

    Implementation:
    - Uses recursive CTE over people.recruited_by_person_id.
    - SQLite supports recursive CTEs; Postgres does as well.
    """
    # Downstream people = all descendants recruited (directly or indirectly)
    downstream_people_sql = text(
        """
        WITH RECURSIVE descendants(id) AS (
            SELECT id FROM people WHERE recruited_by_person_id = :root_id
            UNION ALL
            SELECT p.id FROM people p
            INNER JOIN descendants d ON p.recruited_by_person_id = d.id
        )
        SELECT COUNT(*) AS cnt FROM descendants;
        """
    )
    downstream_people_row = session.exec(downstream_people_sql, params={"root_id": person_id}).one()
    downstream_people = int(downstream_people_row[0] or 0)

    # Downstream voters = voters owned by root OR by descendants
    downstream_voters_sql = text(
        """
        WITH RECURSIVE descendants(id) AS (
            SELECT id FROM people WHERE id = :root_id
            UNION ALL
            SELECT p.id FROM people p
            INNER JOIN descendants d ON p.recruited_by_person_id = d.id
        )
        SELECT COUNT(*) AS cnt
        FROM voter_contacts vc
        WHERE vc.owner_person_id IN (SELECT id FROM descendants);
        """
    )
    downstream_voters_row = session.exec(downstream_voters_sql, params={"root_id": person_id}).one()
    downstream_voters = int(downstream_voters_row[0] or 0)

    # Early simple score. Later you can weight voters differently or use ImpactAction reach.
    score = downstream_people + downstream_voters

    return ImpactSummary(
        person_id=person_id,
        downstream_people=downstream_people,
        downstream_voters=downstream_voters,
        impact_reach_score=int(score),
    )


def top_percent_cutoff(session: Session, percentile: float = 0.90) -> Optional[int]:
    """
    Returns the minimum score required to be in the top X percent.

    Notes:
    - For early data, this will be noisy; still useful for initial leaderboard.
    - percentile is clamped to [0.0, 1.0].
    """
    try:
        p = float(percentile)
    except Exception:
        p = 0.90
    if p < 0.0:
        p = 0.0
    if p > 1.0:
        p = 1.0

    scores_sql = text(
        """
        WITH scores AS (
            SELECT
                p.id AS person_id,
                (
                    -- downstream people (descendants of p)
                    (SELECT COUNT(*) FROM (
                        WITH RECURSIVE descendants(id) AS (
                            SELECT id FROM people WHERE recruited_by_person_id = p.id
                            UNION ALL
                            SELECT p2.id FROM people p2
                            INNER JOIN descendants d ON p2.recruited_by_person_id = d.id
                        )
                        SELECT id FROM descendants
                    )) +
                    -- downstream voters (owned by p or descendants)
                    (SELECT COUNT(*) FROM voter_contacts vc
                        WHERE vc.owner_person_id IN (
                            WITH RECURSIVE descendants2(id) AS (
                                SELECT id FROM people WHERE id = p.id
                                UNION ALL
                                SELECT p3.id FROM people p3
                                INNER JOIN descendants2 d2 ON p3.recruited_by_person_id = d2.id
                            )
                            SELECT id FROM descendants2
                        )
                    )
                ) AS score
            FROM people p
        )
        SELECT score FROM scores ORDER BY score ASC;
        """
    )

    rows = list(session.exec(scores_sql))
    if not rows:
        return None

    scores = [int((r[0] or 0)) for r in rows]
    n = len(scores)
    if n == 1:
        return scores[0]

    # index for percentile cutoff (0.90 -> near top)
    idx = int(p * (n - 1))
    if idx < 0:
        idx = 0
    if idx > n - 1:
        idx = n - 1
    return scores[idx]
