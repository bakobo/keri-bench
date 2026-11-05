# The bench witnesses' graceful stop removes their --store home: heti Witnesses.stop closes every Habery and Mailboxer with clear=True, so a run's end-of-run witness store sizes are lost, and a witness stopped (not killed) and restarted would come back empty, against this.i 5ed29j5w. Found in bench-stock-2's run of 2026-11-05; crash faults SIGKILL, so no fault so far was affected.
kind: todo
created: 2026-11-05T19:43Z

- 2026-11-05T21:51Z 2026-11-05 PM: heti cfb381d (utah/next, imbu-prj restart-loose-ends) makes Witnesses.stop keep a --store home; close this once keri-bench's heti pin moves to cfb381d or later.
