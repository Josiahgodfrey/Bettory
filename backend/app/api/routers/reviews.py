"""Team-name review queue: names a source used that could not be matched to a team safely.

Resolving a review maps the provider's name/id to an existing team and adds the name as an alias,
so the next run of that source matches it automatically. Records that waited for the review are
linked on that source's next run (use `reparse: true` to link them from already stored payloads).
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy.exc import NoResultFound

from app.api.deps import EngineDep, OwnerDep
from app.api.schemas import ResolveReview, ResolveReviewNewTeam, ReviewOut, TeamOut
from app.storage.repositories import reference as ref

router = APIRouter(tags=["reviews"])


@router.get("/reviews", response_model=list[ReviewOut])
def open_reviews(_: OwnerDep, engine: EngineDep) -> list[ReviewOut]:
    """Open reviews, oldest first."""
    with engine.connect() as conn:
        return [ReviewOut(**r) for r in ref.open_reviews(conn)]


@router.post("/reviews/{review_id}/resolve", response_model=dict[str, str])
def resolve_review(review_id: UUID, body: ResolveReview, _: OwnerDep, engine: EngineDep) -> dict[str, str]:
    """Map the reviewed name to an existing team (find its id with GET /teams)."""
    try:
        with engine.begin() as conn:
            ref.resolve_review(conn, review_id, body.team_id)
    except NoResultFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no open review with this id, or no team with this id") from None
    return {"status": "RESOLVED"}


@router.post("/reviews/{review_id}/new-team", response_model=dict[str, str])
def resolve_review_new_team(review_id: UUID, body: ResolveReviewNewTeam, _: OwnerDep,
                            engine: EngineDep) -> dict[str, str]:
    """The name is a club new to the database (e.g. promoted): create the team and resolve the review.
    409 with the similar teams when existing teams share a distinctive word (confirm with allow_similar)."""
    try:
        with engine.begin() as conn:
            team_id = ref.resolve_review_new_team(conn, review_id, body.name, body.allow_similar)
    except NoResultFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no open team review with this id") from None
    except ref.SimilarTeamsExist as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
    return {"status": "RESOLVED", "team_id": str(team_id)}


@router.get("/teams", response_model=list[TeamOut])
def teams(
    _: OwnerDep,
    engine: EngineDep,
    q: Annotated[str | None, Query(max_length=80, description="Part of the team name, e.g. 'man utd'")] = None,
    country: Annotated[str | None, Query(max_length=60, examples=["England"])] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[TeamOut]:
    """Search teams by name or any known alias."""
    try:
        with engine.connect() as conn:
            return [TeamOut(**t) for t in ref.search_teams(conn, q, country, limit)]
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
