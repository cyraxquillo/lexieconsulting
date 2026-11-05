# PythonAnywhere Firestore Setup

These instructions configure Firebase Admin for the Django backend on PythonAnywhere. Replace `<username>` and `<virtualenv>` with your account name and the virtualenv path selected in the PythonAnywhere Web tab. Never place the service-account JSON in the repository or under a directory mapped as static files.

Keep `USE_FIRESTORE=false`; Phase A mirrors SQL data to Firestore but does not switch application reads. Keep `FIRESTORE_SYNC_ENABLED=false` until the outbox migration has been applied and the Firestore connection has passed its health check. The current views remain SQL-backed.

## 1. Prepare service-account credentials

Create or select a Firebase service account and keep its JSON private. Configure the full JSON object as the `FIREBASE_CREDENTIALS` environment variable in the hosting provider's secure environment-variable settings. Do not save it in the repository, a Django static/media directory, or a WSGI source file. Do not print it or paste it into a ticket.

## 2. Install Firebase Admin in the web app virtualenv

Use the same virtualenv selected in the PythonAnywhere **Web** tab. From a Bash console:

```bash
cd /home/<username>/webapp/backend
/home/<username>/.virtualenvs/<virtualenv>/bin/python -m pip install -r requirements.txt
/home/<username>/.virtualenvs/<virtualenv>/bin/python -c "import firebase_admin; print(firebase_admin.__version__)"
```

If your virtualenv is not under `.virtualenvs`, replace that path with its actual path. The Web tab must point to this same environment.

## 3. Set environment variables

Configure these values in the hosting provider's secure environment-variable settings before Django starts:

- `FIREBASE_PROJECT_ID=lexie-13e6b`
- `FIREBASE_CREDENTIALS=<full service-account JSON object>`
- `FIREBASE_DATABASE_URL=<Firebase Realtime Database URL>`
- `USE_FIRESTORE=false`
- `FIRESTORE_SYNC_ENABLED=false`

Never put the service-account JSON or private key in source code. Keep `USE_FIRESTORE=false`; setting it to `true` is not part of Phase A. After deploying the Phase A code, applying its SQL migrations, and passing `check_firebase`, set `FIRESTORE_SYNC_ENABLED=true` to enable the one-way SQL-to-Firestore mirror. This does not change views or reads.

Keep `USE_FIRESTORE=false`; setting it to `true` is not part of Phase A. After deploying the Phase A code, applying its SQL migrations, and passing `check_firebase`, set `FIRESTORE_SYNC_ENABLED=true` to enable the one-way SQL-to-Firestore mirror. This does not change views or reads.

## 4. Test from a Bash console

Environment variables configured for the web app may not be present in a separate Bash console. Set the same values through a secure environment-variable mechanism for the test process, activate the web app's virtualenv, and run the health check:

```bash
cd /home/<username>/webapp/backend
source /home/<username>/.virtualenvs/<virtualenv>/bin/activate
python manage.py check_firebase
```

Success prints:

```text
PASS: Firestore write/read/delete succeeded.
```

The command creates a random document in the `healthcheck` collection, reads it, and deletes it. On failure, it prints `FAIL:` with the exception type and message. It retries cleanup if the write/read/delete operation fails; a reported cleanup error means a temporary health-check document may remain and should be removed from the Firestore console after inspecting the collection.

Common failures:

| Error | Likely cause and action |
| --- | --- |
| `FIREBASE_CREDENTIALS must contain service-account JSON` | The variable is unset or empty in the process running the command. Configure it through a secure environment-variable setting. |
| `FIREBASE_CREDENTIALS must contain valid JSON` | The environment value is not valid JSON. Store the complete service-account JSON object without truncation. |
| JSON parsing, `ValueError`, or `DefaultCredentialsError` | The JSON is not a valid service-account key or is incomplete. Create a new key and update the secure environment variable without displaying its contents. |
| `PermissionDenied` / HTTP 403 | Confirm the service account belongs to project `lexie-13e6b`, Firestore is enabled, and the account has Firestore data access sufficient to create, read, and delete documents (for example, `roles/datastore.user`). |
| `NotFound` / HTTP 404 | Confirm the project ID and Firestore database exist and that the Firestore API is enabled for the project. |
| `Unavailable`, `DeadlineExceeded`, DNS, socket, or timeout errors | The PythonAnywhere plan or network may block outbound access, or a transient Google API/network issue occurred. Check outbound networking below, then retry once connectivity is available. |
| Failure mentioning cleanup | The check could not delete its test document. Inspect `healthcheck` in the Firestore console and remove only the random test document left by this check. |

After a successful check, click **Reload** in the PythonAnywhere Web tab. Review the web app error log if the application does not start.

## 5. Run and retry SQL-to-Firestore sync

Phase A records an outbox entry in the same SQL transaction as each Employee/Evaluation save or delete. After that transaction commits, Django makes one best-effort sync attempt. A failure is logged and retained in the SQL outbox; it does not roll back or block the existing SQL write. The scheduled command retries pending entries with backoff.

Run a retry from a PythonAnywhere Bash console:

```bash
cd /home/<username>/webapp/backend
source /home/<username>/.virtualenvs/<virtualenv>/bin/activate
python manage.py sync_firestore_outbox --batch-size 100
```

In the PythonAnywhere **Tasks** tab, a scheduled command can use:

```bash
cd /home/<username>/webapp/backend && /home/<username>/.virtualenvs/<virtualenv>/bin/python manage.py sync_firestore_outbox --batch-size 500
```

If your plan permits only one daily scheduled task, use it for `sync_firestore_outbox`. The on-commit attempt still runs after each successful SQL save/delete; a failed immediate attempt will be retried on the next daily run, so Firestore may lag by up to a day. The sync command also processes the queued dashboard-summary rebuild. Run `rebuild_dashboard_summaries` manually after the initial migration or whenever you need to refresh the cache immediately.

To inspect differences without modifying Firestore:

```bash
python manage.py reconcile_firestore
```

Only use `--apply` after reviewing the report. It repairs Firestore from SQL and removes Firestore-only Employee/Evaluation documents; it never deletes SQL records.

## 6. Firestore client security rules

The browser must not read or write these Firestore collections. The Django Admin SDK authenticates as a service account and bypasses Firestore Security Rules; its access is controlled by Google Cloud IAM instead. Recommended rules:

```text
rules_version = '2';
service cloud.firestore {
	match /databases/{database}/documents {
		match /{document=**} {
			allow read, write: if false;
		}
	}
}
```

To inspect the deployed rules, open **Firebase Console → project `lexie-13e6b` → Build → Firestore Database → Rules**. Confirm the published rules deny all client reads and writes. Use the Rules Playground or a client SDK test to verify denial; `check_firebase` uses the Admin SDK and is not a test of client rules.

## 7. If outbound Firestore access is blocked

Firestore Admin traffic must reach Google's APIs over outbound HTTPS (and may use gRPC transport). PythonAnywhere free plans can restrict outbound internet access; the exact restrictions depend on the current account and plan.

If the check reports DNS, socket, timeout, or `Unavailable` errors:

1. Check PythonAnywhere's current outbound-network policy for your plan and whether Google's Firestore API endpoints are permitted.
2. Ask PythonAnywhere support whether outbound Firestore/gRPC traffic is supported for your account, or move to a plan that permits the required connections.
3. Keep `USE_FIRESTORE=false` and continue using SQL until `check_firebase` passes. Do not work around the restriction by exposing a service-account key or sending it through a browser/client.
4. Once outbound access is available, rerun the Bash-console command above. A passing check confirms the server can authenticate and perform Firestore document operations.

Firestore security rules should deny direct client reads and writes if the browser never accesses Firestore. The Django backend uses the Admin SDK, which authenticates with IAM and bypasses Firestore security rules; control backend access with service-account IAM permissions instead.
