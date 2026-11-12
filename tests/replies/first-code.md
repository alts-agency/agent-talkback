Here is the new poller.

```python
def poll(api, interval=2.0):
    while True:
        for job in api.jobs(state="queued"):
            api.run(job)
        time.sleep(interval)


def poll_forever(api):
    try:
        poll(api)
    except KeyboardInterrupt:
        return
```
