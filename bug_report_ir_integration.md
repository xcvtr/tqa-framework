
IR Strategy Bug Report:

Root Cause Analysis:
1. The engine.run_days function in strategies/trend_sma/engine.py is hardcoded to only use 
   strategies.trend_sma.detect as the signal generator (lines 413-424).
   
2. There is no mechanism in the engine to dynamically switch signal sources or specify 
   different detect modules.

3. The engine's Config parameter has a `signal_source` attribute but it's only used for 
   external signal sources (e.g., PostgreSQL), not for alternative local detect modules.

4. The engine does not allow for configuration to override which specific detect function 
   to use when running with local detection.

Fix Recommendation:
1. Modify the engine's run_days to check for a specific detector configuration that allows
   specifying an alternative detect module instead of hardcoding just the trend_sma detect.

2. Add logic to support loading and using strategies/ir/detect.py's detect function when
   that's the intended strategy.

3. Either:
   a) Add a special parameter in Config to indicate which detect module to use (like 
      'ir', 'trend_sma', etc.), or
   b) Add logic to recognize when the strategy is 'ir' and then use the IR detect module.
