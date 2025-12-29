from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
from sqlmodel import Session, text

@dataclass(frozen=True)
class ImpactSummary:
    person_id: int
    downstream_people: int
    downstream_voters: int
    impact_reach_score: int

def compute_impact(session: Session, person_id: int) -> ImpactSummary:
    # Downstream people = all descendants recruited (directly or indirectly)
    # Uses recursive CTE on people.recruited_by_person_id
    downstream_people_sql = text("""
    WITH RECURSIVE descendants(id) AS (
        SELECT id FROM people WHERE recruited_by_person_id = :root_id
        UNION ALL
        SELECT p.id FROM people p
        INNER JOIN descendants d ON p.recruited_by_person_id = d.id
    )
    SELECT COUNT(*) AS cnt FROM descendants;
    """)
    downstream_people = session.exec(downstream_people_sql, params={"root_id": person_id}).one()[0]

    # Downstream voters = voters owned by root OR by descendants
    downstream_voters_sql = text("""
    WITH RECURSIVE descendants(id) AS (
        SELECT id FROM people WHERE id = :root_id
        UNION ALL
        SELECT p.id FROM people p
        INNER JOIN descendants d ON p.recruited_by_person_id = d.id
    )
    SELECT COUNT(*) AS cnt
    FROM voter_contacts vc
    WHERE vc.owner_person_id IN (SELECT id FROM descendants);
    """)
    downstream_voters = session.exec(downstream_voters_sql, params={"root_id": person_id}).one()[0]

    score = int(downstream_people) + int(downstream_voters)
    return ImpactSummary(
        person_id=person_id,
        downstream_people=int(downstream_people),
        downstream_voters=int(downstream_voters),
        impact_reach_score=score,
    )

def top_percent_cutoff(session: Session, percentile: float = 0.90) -> Optional[int]:
    # Returns the minimum score required to be in the top X percent.
    # For early data, this will be noisy; still useful for initial leaderboard.
    scores_sql = text("""
    WITH scores AS (
        SELECT p.id AS person_id,
               (
                 -- downstream people + downstream voters
                 (SELECT COUNT(*) FROM (
                    WITH RECURSIVE descendants(id) AS (
                        SELECT id FROM people WHERE recruited_by_person_id = p.id
                        UNION ALL
                        SELECT p2.id FROM people p2
                        INNER JOIN descendants d ON p2.recruited_by_person_id = d.id
                    )
                    SELECT id FROM descendants
                 )) +
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
    """)
    rows = list(session.exec(scores_sql))
    if not rows:
        return None
    scores = [int(r[0]) for r in rows]
    n = len(scores)
    # index for percentile cutoff
    idx = int(percentile * (n - 1))
    return scores[idx]
