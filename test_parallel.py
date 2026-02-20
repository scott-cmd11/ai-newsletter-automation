
import sys
import os
import time
from datetime import datetime

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "."))

from ai_newsletter_automation.runner import process_section
from ai_newsletter_automation.config import get_settings

def test_parallel_generation():
    print(f"[{datetime.now().time()}] Starting parallel generation test...")
    start_time = time.time()
    
    try:
        # 'events' usually has fewer items, good for quick test
        # 'trending' triggers search, also good.
        items = process_section("trending", 7, max_per_stream=2, lang="en")
        
        elapsed = time.time() - start_time
        print(f"[{datetime.now().time()}] Finished in {elapsed:.2f} seconds.")
        print(f"Generated {len(items)} items.")
        for item in items:
            print(f"- {item.Headline} ({item.Live_Link})")
            
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_parallel_generation()
