import os
import logging
import shutil
from pathlib import Path
from typing import Dict, Any

from fastapi import APIRouter, HTTPException, status
from graph_service.dto import Result

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/maintenance", tags=["maintenance"])

@router.post('/cleanup-corrupted-files', status_code=status.HTTP_200_OK)
async def cleanup_corrupted_files():
    """
    API endpoint to clean up corrupted FalkorDB backup files
    This can be called manually to trigger cleanup without restarting the service
    """
    
    logger.info("🧹 Manual cleanup of corrupted FalkorDB files requested")
    
    try:
        # FalkorDB data directory - try both possible paths
        data_dir = None
        possible_paths = [
            Path("/var/lib/falkordb/data"),
            Path("/var/lib/falkordb"),
            Path(os.environ.get("RAILWAY_VOLUME_MOUNT_PATH", "/var/lib/falkordb/data"))
        ]
        
        for path in possible_paths:
            if path.exists():
                data_dir = path
                break
        
        if not data_dir:
            logger.warning("FalkorDB data directory not found")
            return Result(
                message="FalkorDB data directory not found - no cleanup needed", 
                success=True
            )
        
        print(f"🗂️ Cleaning up corrupted files in: {data_dir}")
        
        # Count files before cleanup
        corrupted_items = list(data_dir.glob("*runtime_corrupted*"))
        corrupted_dirs = [item for item in corrupted_items if item.is_dir()]
        corrupted_files = [item for item in corrupted_items if item.is_file()]
        
        if not corrupted_dirs and not corrupted_files:
            logger.info("✅ No corrupted files found - data directory is clean")
            return Result(
                message="No corrupted files found - data directory is already clean", 
                success=True
            )
        
        # Calculate total size before cleanup
        total_size = 0
        for item in corrupted_items:
            if item.is_file():
                total_size += item.stat().st_size
            elif item.is_dir():
                for file in item.rglob("*"):
                    if file.is_file():
                        total_size += file.stat().st_size
        
        print(f"🔍 Found {len(corrupted_dirs)} corrupted directories and {len(corrupted_files)} corrupted files")
        print(f"💾 Total corrupted data size: {total_size / (1024*1024):.2f} MB")
        
        # Remove corrupted directories
        removed_dirs = 0
        for corrupted_dir in corrupted_dirs:
            try:
                shutil.rmtree(corrupted_dir)
                removed_dirs += 1
            except Exception as e:
                print(f"❌ Failed to remove directory {corrupted_dir}: {e}")
        
        # Remove corrupted files
        removed_files = 0
        for corrupted_file in corrupted_files:
            try:
                corrupted_file.unlink()
                removed_files += 1
            except Exception as e:
                print(f"❌ Failed to remove file {corrupted_file}: {e}")
        
        # Show results
        cleanup_summary = {
            "removed_directories": removed_dirs,
            "removed_files": removed_files,
            "total_removed": removed_dirs + removed_files,
            "space_freed_mb": round(total_size / (1024*1024), 2),
            "data_directory": str(data_dir)
        }
        
        print(f"✅ Cleanup completed!")
        print(f"🗂️ Removed {removed_dirs} corrupted directories")
        print(f"📄 Removed {removed_files} corrupted files")
        print(f"💾 Freed up ~{total_size / (1024*1024):.2f} MB of disk space")
        
        return Result(
            message=f"Successfully cleaned up {removed_dirs + removed_files} corrupted items, freed {total_size / (1024*1024):.2f} MB", 
            success=True,
            data=cleanup_summary
        )
        
    except Exception as e:
        logger.error(f"❌ Error during cleanup: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to cleanup corrupted files: {str(e)}"
        )

@router.get('/data-directory-status', status_code=status.HTTP_200_OK)
async def get_data_directory_status():
    """
    Get status of the FalkorDB data directory including corrupted files count
    """
    
    try:
        # Find data directory
        data_dir = None
        possible_paths = [
            Path("/var/lib/falkordb/data"),
            Path("/var/lib/falkordb"),
            Path(os.environ.get("RAILWAY_VOLUME_MOUNT_PATH", "/var/lib/falkordb/data"))
        ]
        
        for path in possible_paths:
            if path.exists():
                data_dir = path
                break
        
        if not data_dir:
            return Result(
                message="FalkorDB data directory not found", 
                success=False
            )
        
        # Count different types of files
        all_items = list(data_dir.iterdir())
        corrupted_items = list(data_dir.glob("*runtime_corrupted*"))
        corrupted_dirs = [item for item in corrupted_items if item.is_dir()]
        corrupted_files = [item for item in corrupted_items if item.is_file()]
        
        # Calculate sizes
        total_size = sum(item.stat().st_size for item in all_items if item.is_file())
        corrupted_size = 0
        for item in corrupted_items:
            if item.is_file():
                corrupted_size += item.stat().st_size
            elif item.is_dir():
                for file in item.rglob("*"):
                    if file.is_file():
                        corrupted_size += file.stat().st_size
        
        status_info = {
            "data_directory": str(data_dir),
            "total_items": len(all_items),
            "corrupted_directories": len(corrupted_dirs),
            "corrupted_files": len(corrupted_files),
            "total_corrupted_items": len(corrupted_items),
            "total_size_mb": round(total_size / (1024*1024), 2),
            "corrupted_size_mb": round(corrupted_size / (1024*1024), 2),
            "corrupted_items_list": [item.name for item in corrupted_items],
            "cleanup_recommended": len(corrupted_items) > 0
        }
        
        return Result(
            message=f"Data directory contains {len(corrupted_items)} corrupted items ({corrupted_size / (1024*1024):.2f} MB)", 
            success=True,
            data=status_info
        )
        
    except Exception as e:
        logger.error(f"❌ Error getting data directory status: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get data directory status: {str(e)}"
        )