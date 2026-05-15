from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session

from app import crud
from app.api_serializers import to_rule_out
from app.api_validators import validate_rule_payload
from app.db import get_session
from app.schemas import LabelRuleIn, LabelRuleOut

router = APIRouter(prefix="/api", tags=["rules"])


@router.get("/rules", response_model=list[LabelRuleOut])
def get_rules(session: Session = Depends(get_session)) -> list[LabelRuleOut]:
    destinations = {destination.id: destination for destination in crud.list_destinations(session)}
    result: list[LabelRuleOut] = []
    for rule in crud.list_rules(session):
        destination = destinations.get(rule.destination_id)
        result.append(to_rule_out(rule, destination.name if destination else None))
    return result


@router.post("/rules", response_model=LabelRuleOut)
def post_rule(payload: LabelRuleIn, session: Session = Depends(get_session)) -> LabelRuleOut:
    validate_rule_payload(session, payload)
    try:
        rule = crud.create_rule(session, payload)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Failed to create rule: {exc}") from exc
    destination = crud.get_destination(session, rule.destination_id)
    return to_rule_out(rule, destination.name if destination else None)


@router.put("/rules/{rule_id}", response_model=LabelRuleOut)
def put_rule(rule_id: int, payload: LabelRuleIn, session: Session = Depends(get_session)) -> LabelRuleOut:
    validate_rule_payload(session, payload)
    rule = crud.update_rule(session, rule_id, payload)
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")
    destination = crud.get_destination(session, rule.destination_id)
    return to_rule_out(rule, destination.name if destination else None)


@router.delete("/rules/{rule_id}")
def remove_rule(rule_id: int, session: Session = Depends(get_session)) -> dict[str, bool]:
    ok = crud.delete_rule(session, rule_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Rule not found")
    return {"deleted": True}
