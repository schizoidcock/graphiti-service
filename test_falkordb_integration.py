#!/usr/bin/env python3
"""
Simple test script to verify FalkorDB integration
"""
import sys
import os

# Add the project root to the Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def test_imports():
    """Test that all the necessary modules can be imported"""
    try:
        print("🔍 Testing imports...")
        
        # Test core driver imports
        from graphiti_core.driver.falkordb_driver import FalkorDriver
        print("✅ FalkorDriver imported successfully")
        
        from graphiti_core.driver import FalkorDriver as DriverFromInit
        print("✅ FalkorDriver imported from __init__ successfully")
        
        # Test main graphiti import
        from graphiti_core.graphiti import Graphiti
        print("✅ Graphiti imported successfully")
        
        # Test service imports
        from graph_service.config import Settings
        print("✅ Settings imported successfully")
        
        from graph_service.zep_graphiti import ZepGraphiti
        print("✅ ZepGraphiti imported successfully")
        
        print("🎉 All imports successful!")
        return True
        
    except ImportError as e:
        print(f"❌ Import error: {e}")
        return False
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        return False

def test_driver_initialization():
    """Test that FalkorDriver can be initialized"""
    try:
        print("\n🔍 Testing FalkorDriver initialization...")
        
        from graphiti_core.driver.falkordb_driver import FalkorDriver
        
        # Test basic initialization (without connecting)
        driver = FalkorDriver(
            host="localhost",
            port=6379,
            username=None,
            password=None,
            database="test_db"
        )
        
        print("✅ FalkorDriver initialized successfully")
        print(f"   - Provider: {driver.provider}")
        print(f"   - Database: {driver._database}")
        print(f"   - Fulltext syntax: '{driver.fulltext_syntax}'")
        
        return True
        
    except Exception as e:
        print(f"❌ Driver initialization error: {e}")
        return False

def test_zep_graphiti_class():
    """Test that ZepGraphiti can be instantiated"""
    try:
        print("\n🔍 Testing ZepGraphiti class...")
        
        from graph_service.zep_graphiti import ZepGraphiti
        
        # Test ZepGraphiti initialization (without connecting)
        zep_graphiti = ZepGraphiti(
            host="localhost",
            port="6379",
            username=None,
            password=None,
            skip_init=True,  # Skip actual connection
            user_id="test_user"
        )
        
        print("✅ ZepGraphiti initialized successfully")
        print(f"   - Driver type: {type(zep_graphiti.driver).__name__}")
        print(f"   - Provider: {zep_graphiti.driver.provider}")
        
        return True
        
    except Exception as e:
        print(f"❌ ZepGraphiti initialization error: {e}")
        return False

def main():
    """Run all tests"""
    print("🚀 Testing FalkorDB Integration")
    print("=" * 40)
    
    tests = [
        test_imports,
        test_driver_initialization,
        test_zep_graphiti_class,
    ]
    
    passed = 0
    total = len(tests)
    
    for test in tests:
        if test():
            passed += 1
        print()
    
    print("=" * 40)
    print(f"📊 Test Results: {passed}/{total} tests passed")
    
    if passed == total:
        print("🎉 All tests passed! FalkorDB integration looks good.")
        return 0
    else:
        print("❌ Some tests failed. Check the errors above.")
        return 1

if __name__ == "__main__":
    sys.exit(main())