from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Thread
from time import sleep

from autonomous_agent.background_worker import BackgroundTaskWorker
from autonomous_agent.task_core import AutonomousTaskCore
from autonomous_agent.task_queue import TaskQueueStore


def main() -> int:
    with TemporaryDirectory(prefix="scout-n20-") as directory:
        queue_path = Path(directory) / "queue.json"
        queue = TaskQueueStore(queue_path)
        core = AutonomousTaskCore()
        core.submit_background(
            "inspect repository",
            queue=queue,
            task_id="demo-1",
            execution_id="n20-exec-1",
        )
        core.submit_background(
            "inspect repository",
            queue=queue,
            task_id="demo-2",
            execution_id="n20-exec-2",
        )

        def handler(item):
            print(f"WORKER picked {item.task_id}: {item.task}")
            return True

        worker = BackgroundTaskWorker(queue, handler, poll_interval=0.01)
        stop = Event()
        thread = Thread(target=worker.run_forever, args=(stop,), daemon=True)
        thread.start()
        for _ in range(100):
            states = [item.state.value for item in queue.list()]
            if states == ["succeeded", "succeeded"]:
                break
            sleep(0.01)
        stop.set()
        thread.join(timeout=1)

        print("N20 Background Worker + Queue demo")
        for item in queue.list():
            print(f"{item.task_id}: {item.state.value}, attempts={item.attempts}")
        return 0 if all(item.state.value == "succeeded" for item in queue.list()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
