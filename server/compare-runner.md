# Unit 4 additions to the comparison list

Unit 4 adds two step kinds to the existing request list. Ordinary HTTP requests keep their current form.

A stream step opens an SSE route on both servers. It reads complete frames until the frame count or total deadline, then closes both streams. The comparison includes status, selected headers, frame order, event names, and parsed JSON data. Comment heartbeats do not count as frames. The step must collect the requested frame count on both sides. Two empty streams must not pass through equal silence.

```json
{"kind":"stream","method":"GET","path":"/api/navigation/view/events?view=...","frames":1,"deadline_seconds":5}
```

A restart step stops both fixture processes and starts their original host modules on their existing databases. Captures and comparison continuity remain available to subsequent requests. The fixture owns each process and listener. This step never signals an unrelated server.

```json
{"kind":"restart"}
```

The stream step is a bounded observation. It does not prove an update during an open connection, a blocked transport deadline, or cancellation during a send. The restart step proves recovery through subsequent reads. It does not prove process death during an uncommitted write.

These step kinds remain separate from the per-list Python host selection that the conversation contract unit owns. Unit 4 uses the default Python host.
