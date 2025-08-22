import uuid as uuid_lib
import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional

from fastapi import APIRouter, HTTPException, status, Query
from pydantic import BaseModel, Field
from graph_service.config import ZepEnvDep

# Logger for user management
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v2", tags=["users"])

# In-memory user storage (in production, use a database)
users_store: Dict[str, Dict[str, Any]] = {}


class UserCreateRequest(BaseModel):
    """Request model for creating a user"""
    user_id: str = Field(..., description='Unique user identifier')
    email: Optional[str] = Field(None, description='User email address')
    first_name: Optional[str] = Field(None, description='User first name')
    last_name: Optional[str] = Field(None, description='User last name')
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict, description='User metadata')
    fact_rating_instruction: Optional[str] = Field(None, description='Instructions for fact rating')


class UserUpdateRequest(BaseModel):
    """Request model for updating a user"""
    email: Optional[str] = Field(None, description='User email address')
    first_name: Optional[str] = Field(None, description='User first name')
    last_name: Optional[str] = Field(None, description='User last name')
    metadata: Optional[Dict[str, Any]] = Field(None, description='User metadata')
    fact_rating_instruction: Optional[str] = Field(None, description='Instructions for fact rating')


class UserResponse(BaseModel):
    """Response model for user data"""
    uuid: str = Field(..., description='Server-generated UUID')
    id: int = Field(..., description='Internal database ID')
    user_id: str = Field(..., description='User identifier')
    email: Optional[str] = Field(None, description='User email address')
    first_name: Optional[str] = Field(None, description='User first name')
    last_name: Optional[str] = Field(None, description='User last name')
    created_at: datetime = Field(..., description='User creation timestamp')
    updated_at: datetime = Field(..., description='Last update timestamp')
    metadata: Dict[str, Any] = Field(default_factory=dict, description='User metadata')
    session_count: int = Field(default=0, description='Number of sessions for this user')


class UserListResponse(BaseModel):
    """Response model for listing users"""
    users: list[UserResponse] = Field(..., description='List of users')
    total_count: int = Field(..., description='Total number of users')
    row_count: int = Field(..., description='Number of users in current response')


@router.post('/users', status_code=status.HTTP_201_CREATED, response_model=UserResponse)
async def create_user(request: UserCreateRequest):
    """Create a new user following official Zep API"""
    
    # Check if user already exists
    if request.user_id in users_store:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"User with id '{request.user_id}' already exists"
        )
    
    # Generate server identifiers
    user_uuid = str(uuid_lib.uuid4())
    user_id = len(users_store) + 1
    current_time = datetime.now(timezone.utc)
    
    # Store user data
    user_data = {
        "uuid": user_uuid,
        "id": user_id,
        "user_id": request.user_id,
        "email": request.email,
        "first_name": request.first_name,
        "last_name": request.last_name,
        "created_at": current_time,
        "updated_at": current_time,
        "metadata": request.metadata or {},
        "session_count": 0
    }
    
    users_store[request.user_id] = user_data
    logger.info(f"Created user {request.user_id}")
    
    return UserResponse(**user_data)


@router.get('/users/{user_id}', status_code=status.HTTP_200_OK, response_model=UserResponse)
async def get_user(user_id: str):
    """Get user by ID"""
    
    if user_id not in users_store:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User '{user_id}' not found"
        )
    
    # Calculate session count (from sessions store if available)
    user_data = users_store[user_id].copy()
    
    # Import sessions store to count sessions
    try:
        from graph_service.routers.sessions import sessions_store
        session_count = sum(1 for session in sessions_store.values() 
                          if session.get("user_id") == user_id)
        user_data["session_count"] = session_count
    except ImportError:
        pass
    
    return UserResponse(**user_data)


@router.get('/users-ordered', status_code=status.HTTP_200_OK, response_model=UserListResponse)
async def list_users_ordered(
    pageNumber: int = Query(1, description="Page number starting from 1"),
    pageSize: int = Query(100, description="Number of users per page")
):
    """List users with ordering and pagination following official Zep API"""
    
    # Convert page-based to offset-based pagination
    offset = (pageNumber - 1) * pageSize
    
    # Get all users and sort by creation date
    all_users = []
    for user_data in users_store.values():
        # Calculate session count
        try:
            from graph_service.routers.sessions import sessions_store
            session_count = sum(1 for session in sessions_store.values() 
                              if session.get("user_id") == user_data["user_id"])
            user_data["session_count"] = session_count
        except ImportError:
            pass
        
        all_users.append(UserResponse(**user_data))
    
    # Sort by created_at (most recent first)
    all_users.sort(key=lambda x: x.created_at, reverse=True)
    
    # Apply pagination
    total_count = len(all_users)
    paginated_users = all_users[offset:offset + pageSize]
    
    return UserListResponse(
        users=paginated_users,
        total_count=total_count,
        row_count=len(paginated_users)
    )


@router.patch('/users/{user_id}', status_code=status.HTTP_200_OK, response_model=UserResponse)
async def update_user(user_id: str, request: UserUpdateRequest):
    """Update user using PATCH method following REST conventions"""
    
    if user_id not in users_store:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User '{user_id}' not found"
        )
    
    user_data = users_store[user_id]
    
    # Update only provided fields
    if request.email is not None:
        user_data["email"] = request.email
    if request.first_name is not None:
        user_data["first_name"] = request.first_name  
    if request.last_name is not None:
        user_data["last_name"] = request.last_name
    if request.metadata is not None:
        user_data["metadata"] = request.metadata
    
    user_data["updated_at"] = datetime.now(timezone.utc)
    
    logger.info(f"Updated user {user_id}")
    
    # Calculate session count
    try:
        from graph_service.routers.sessions import sessions_store
        session_count = sum(1 for session in sessions_store.values() 
                          if session.get("user_id") == user_id)
        user_data["session_count"] = session_count
    except ImportError:
        pass
    
    return UserResponse(**user_data)


@router.delete('/users/{user_id}', status_code=status.HTTP_200_OK)
async def delete_user(user_id: str):
    """Delete user and return success message"""
    
    if user_id not in users_store:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User '{user_id}' not found"
        )
    
    # Delete user from store
    del users_store[user_id]
    
    # Note: In production, you might want to also clean up associated sessions
    logger.info(f"Deleted user {user_id}")
    
    return {"message": f"User '{user_id}' deleted successfully"}


@router.get('/users/{user_id}/sessions', status_code=status.HTTP_200_OK)
async def get_user_sessions(user_id: str):
    """Get all sessions for a specific user"""
    
    if user_id not in users_store:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User '{user_id}' not found"
        )
    
    # Get sessions for this user
    user_sessions = []
    try:
        from graph_service.routers.sessions import sessions_store
        from graph_service.dto.session import SessionResponse
        
        for session_data in sessions_store.values():
            if session_data.get("user_id") == user_id:
                user_sessions.append(SessionResponse(**session_data))
        
        # Sort by creation date (most recent first)
        user_sessions.sort(key=lambda x: x.created_at, reverse=True)
        
    except ImportError:
        logger.warning("Sessions store not available")
    
    return user_sessions


@router.get('/users/{user_id}/node', status_code=status.HTTP_200_OK)
async def get_user_node(user_id: str, settings: ZepEnvDep):
    """Get user node from FalkorDB graph database following official Zep API v2 specification"""
    
    # Note: User existence is validated by zep-server-railway before proxying here
    # This endpoint only needs to query the graph database for the user's node
    
    try:
        # Import graphiti core for actual FalkorDB queries
        from graph_service.zep_graphiti import get_graphiti_for_user
        
        # Get graphiti instance for the user
        graphiti_instance = await get_graphiti_for_user(user_id, settings)
        
        # Search for the user node in the graph database
        # Query FalkorDB for user entity nodes - search returns async generator
        user_nodes_generator = graphiti_instance.search(
            query=f"user {user_id}",
            user_id=user_id,
            limit=1
        )
        
        # Collect results from the async generator
        user_nodes = []
        async for result in user_nodes_generator:
            user_nodes.extend(result.nodes)
            break  # We only need the first batch since limit=1
        
        if not user_nodes:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User Get Node Request Not Found Error"
            )
        
        # Get the first user node
        user_node = user_nodes[0]
        
        # Format response according to Zep v2 API specification
        node_response = {
            "node": {
                "created_at": user_node.created_at.isoformat() + "Z" if user_node.created_at else None,
                "name": user_node.name,
                "summary": user_node.summary or f"User node for {user_id}",
                "uuid": user_node.uuid,
                "attributes": user_node.attributes or {},
                "labels": user_node.labels or ["User"],
                "score": getattr(user_node, 'score', 1.0)
            }
        }
        
        logger.info(f"Retrieved user node from FalkorDB for: {user_id}")
        return node_response
        
    except ImportError:
        logger.error("Graphiti core not available")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="User Get Node Request internal Server Error"
        )
    except Exception as e:
        logger.error(f"Error retrieving user node for {user_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User Get Node Request Not Found Error"
        )