# 3-minute local demo: suggested run order (real Webex space)
Have `testcases.xlsx` open **read-only preview** next to Webex (close Excel before step 7 or use it for the lock demo).
1. (0:00) Run `python -m bot.app` + `curl localhost:8080/health` to verify server is active.
2. (0:20) Sample 1: full case + screenshot + Jira link -> show reply, then open the Auth sheet row.
3. (0:50) Sample 2: `add test case: checkout is broken` -> follow-up; answer it.
4. (1:10) Sample 3: reworded duplicate -> warning; reply `cancel`.
5. (1:25) Sample 5 (two cases) and sample 9 (`password=Secr3t!` -> show it masked in Excel).
6. (1:55) `where is the Safari login test?`, `where is the payment refund test?` (no match), `show TC-0001`.
7. (2:15) `update TC-0001 status: passed` -> show Audit sheet; `delete TC-0001` -> `yes` -> grey row; `restore`.
8. (2:40) Open workbook in Excel, send a case -> "queued" -> close Excel -> "Saved". Mention backups folder.
