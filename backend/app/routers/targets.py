from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Target
from app.auth import require_admin

router = APIRouter(prefix="/api/targets", tags=["targets"])


class TargetCreate(BaseModel):
    name: str
    known_urls: list[str] = []
    notes: str | None = None
    disease_area: str | None = None
    twitter_handle: str | None = None
    linkedin_url: str | None = None


class TargetUpdate(BaseModel):
    name: str | None = None
    known_urls: list[str] | None = None
    notes: str | None = None
    active: bool | None = None
    disease_area: str | None = None
    twitter_handle: str | None = None
    linkedin_url: str | None = None


class TargetOut(BaseModel):
    id: int
    name: str
    known_urls: list[str]
    notes: str | None
    active: bool
    disease_area: str | None = None
    twitter_handle: str | None = None
    linkedin_url: str | None = None

    model_config = {"from_attributes": True}


@router.get("/", response_model=list[TargetOut])
async def list_targets(db: AsyncSession = Depends(get_db)):
    rows = await db.execute(select(Target).order_by(Target.name))
    targets = rows.scalars().all()
    result = []
    for t in targets:
        import json
        result.append(TargetOut(
            id=t.id, name=t.name,
            known_urls=json.loads(t.known_urls or "[]"),
            notes=t.notes, active=t.active, disease_area=t.disease_area,
            twitter_handle=t.twitter_handle, linkedin_url=t.linkedin_url,
        ))
    return result


@router.post("/", response_model=TargetOut, status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(require_admin)])
async def create_target(body: TargetCreate, db: AsyncSession = Depends(get_db)):
    import json
    target = Target(
        name=body.name, known_urls=json.dumps(body.known_urls), notes=body.notes,
        disease_area=body.disease_area, twitter_handle=body.twitter_handle, linkedin_url=body.linkedin_url,
    )
    db.add(target)
    await db.commit()
    await db.refresh(target)
    return TargetOut(id=target.id, name=target.name,
                     known_urls=json.loads(target.known_urls or "[]"),
                     notes=target.notes, active=target.active, disease_area=target.disease_area,
                     twitter_handle=target.twitter_handle, linkedin_url=target.linkedin_url)


@router.put("/{target_id}", response_model=TargetOut, dependencies=[Depends(require_admin)])
async def update_target(target_id: int, body: TargetUpdate, db: AsyncSession = Depends(get_db)):
    import json
    target = await db.get(Target, target_id)
    if not target:
        raise HTTPException(status_code=404, detail="Target not found")
    if body.name is not None:
        target.name = body.name
    if body.known_urls is not None:
        target.known_urls = json.dumps(body.known_urls)
    if body.notes is not None:
        target.notes = body.notes
    if body.active is not None:
        target.active = body.active
    if body.disease_area is not None:
        target.disease_area = body.disease_area
    if body.twitter_handle is not None:
        target.twitter_handle = body.twitter_handle or None
    if body.linkedin_url is not None:
        target.linkedin_url = body.linkedin_url or None
    await db.commit()
    await db.refresh(target)
    return TargetOut(id=target.id, name=target.name,
                     known_urls=json.loads(target.known_urls or "[]"),
                     notes=target.notes, active=target.active, disease_area=target.disease_area,
                     twitter_handle=target.twitter_handle, linkedin_url=target.linkedin_url)


@router.delete("/{target_id}", status_code=status.HTTP_204_NO_CONTENT,
               dependencies=[Depends(require_admin)])
async def deactivate_target(target_id: int, db: AsyncSession = Depends(get_db)):
    target = await db.get(Target, target_id)
    if not target:
        raise HTTPException(status_code=404, detail="Target not found")
    target.active = False
    await db.commit()
