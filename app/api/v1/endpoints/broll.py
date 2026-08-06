from fastapi import APIRouter, Response, status

from app.api.dependencies import BrollRetrievalServiceDependency
from app.core.exceptions import BrollEnqueueError
from app.models.broll import BrollAsset, BrollCollection, BrollCollectionStatus
from app.schemas.broll import (
    BrollAssetResponse,
    BrollCollectionResponse,
    BrollCollectionStatusResponse,
    BrollRetrievalRequest,
)
from app.tasks.broll_retrieval import retrieve_broll

router = APIRouter()

BrollCollectionAPIResponse = BrollCollectionResponse | BrollCollectionStatusResponse


def _asset_response(asset: BrollAsset) -> BrollAssetResponse:
    return BrollAssetResponse.model_validate(asset)


async def _collection_response(
    collection: BrollCollection, service: BrollRetrievalServiceDependency
) -> BrollCollectionAPIResponse:
    if collection.status != BrollCollectionStatus.COMPLETED:
        return BrollCollectionStatusResponse.model_validate(collection)
    assets = await service.list_assets_for_collection(collection.id)
    return BrollCollectionResponse.model_validate(
        {
            "id": collection.id,
            "script_id": collection.script_id,
            "status": collection.status,
            "provider": collection.provider,
            "query_strategy": collection.query_strategy,
            "retrieval_options": collection.retrieval_options,
            "assets": assets,
            "created_at": collection.created_at,
            "updated_at": collection.updated_at,
            "completed_at": collection.completed_at,
            "error_message": collection.error_message,
        }
    )


async def _enqueue(collection: BrollCollection, service: BrollRetrievalServiceDependency) -> None:
    try:
        retrieve_broll.delay(collection.id)
    except Exception as error:
        await service.mark_broll_enqueue_failed(collection, error)
        raise BrollEnqueueError from error


@router.post(
    "/scripts/{script_id}/broll-collections",
    response_model=BrollCollectionAPIResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_collection(
    script_id: int,
    request: BrollRetrievalRequest,
    service: BrollRetrievalServiceDependency,
) -> BrollCollectionAPIResponse:
    collection = await service.request_broll_retrieval(
        script_id,
        request.options,
        query_strategy=request.query_strategy,
    )
    await _enqueue(collection, service)
    return await _collection_response(collection, service)


@router.get(
    "/scripts/{script_id}/broll-collections",
    response_model=list[BrollCollectionAPIResponse],
)
async def list_collections(
    script_id: int, service: BrollRetrievalServiceDependency
) -> list[BrollCollectionAPIResponse]:
    collections = await service.list_collections_for_script(script_id)
    return [await _collection_response(collection, service) for collection in collections]


@router.get("/broll-collections/{collection_id}", response_model=BrollCollectionAPIResponse)
async def get_collection(
    collection_id: int, service: BrollRetrievalServiceDependency
) -> BrollCollectionAPIResponse:
    collection = await service.get_collection(collection_id)
    return await _collection_response(collection, service)


@router.post(
    "/broll-collections/{collection_id}/retry",
    response_model=BrollCollectionAPIResponse,
)
async def retry_collection(
    collection_id: int,
    response: Response,
    service: BrollRetrievalServiceDependency,
) -> BrollCollectionAPIResponse:
    collection, should_enqueue = await service.prepare_collection_retry(collection_id)
    if not should_enqueue:
        return await _collection_response(collection, service)
    await _enqueue(collection, service)
    response.status_code = status.HTTP_202_ACCEPTED
    return await _collection_response(collection, service)


@router.get(
    "/broll-collections/{collection_id}/assets",
    response_model=list[BrollAssetResponse],
)
async def list_assets(
    collection_id: int, service: BrollRetrievalServiceDependency
) -> list[BrollAssetResponse]:
    assets = await service.list_assets_for_collection(collection_id)
    return [_asset_response(asset) for asset in assets]


@router.get("/broll-assets/{asset_id}", response_model=BrollAssetResponse)
async def get_asset(asset_id: int, service: BrollRetrievalServiceDependency) -> BrollAssetResponse:
    return _asset_response(await service.get_asset(asset_id))


@router.post("/broll-assets/{asset_id}/select", response_model=BrollAssetResponse)
async def select_asset(
    asset_id: int, service: BrollRetrievalServiceDependency
) -> BrollAssetResponse:
    return _asset_response(await service.select_asset_and_commit(asset_id))


@router.post("/broll-assets/{asset_id}/reject", response_model=BrollAssetResponse)
async def reject_asset(
    asset_id: int, service: BrollRetrievalServiceDependency
) -> BrollAssetResponse:
    return _asset_response(await service.reject_asset_and_commit(asset_id))
