Replace only launch\Launch_Sentinuity.bat.

Fix:
- uses the absolute built-in Windows PowerShell path, so PATH cannot break it;
- bounded 30-second gateway cleanup;
- no GOTO labels;
- starts gateway and then continues the launcher.
