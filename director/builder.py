import pdb
from tkinter.constants import W
from celery import chain, group
from celery.utils import uuid

from director.exceptions import WorkflowSyntaxError, WorkflowNotFound
from director.extensions import cel, cel_workflows
from director.models import StatusType
from director.models.tasks import Task
from director.models.workflows import Workflow
from director.tasks.workflows import start, end, sub_flows


class WorkflowBuilder(object):
    def __init__(self, workflow_id):
        self.workflow_id = workflow_id
        self._workflow = None

        self.queue = cel_workflows.get_queue(str(self.workflow))
        self.tasks = cel_workflows.get_tasks(str(self.workflow))
        self.sub_flows = []
        self.canvas = []

        # Pointer to the previous task(s)
        self.previous = []

    @property
    def workflow(self):
        if not self._workflow:
            self._workflow = Workflow.query.filter_by(id=self.workflow_id).first()
        return self._workflow

    def new_task(self, task_name, queue=None, single=True, payload=None):
        if not queue:
            queue = self.queue
        task_id = uuid()

        if not payload:
            payload = self.workflow.payload

        # We create the Celery task specifying its UID
        signature = cel.tasks.get(task_name).subtask(
            kwargs={"workflow_id": self.workflow_id, "payload": payload},
            queue=queue,
            task_id=task_id,
        )

        # Director task has the same UID
        task = Task(
            id=task_id,
            key=task_name,
            previous=self.previous,
            workflow_id=self.workflow.id,
            status=StatusType.pending,
        )
        task.save()

        if single:
            self.previous = [signature.id]

        return signature

    def parse(self, tasks):
        canvas = []
        # import pdb; pdb.set_trace()
        for task in tasks:
            if type(task) is not dict:
                raise WorkflowSyntaxError()

            name = list(task)[0]
            if not task[name].get("type"):
                raise WorkflowSyntaxError()

            task_type = task[name].get("type")
            try:
                if task_type == "workflow":
                    self.sub_flows.append(task[name]["name"])
                    continue

                parse = getattr(self, f"parse_{task[name]['type']}")
                print(parse)
                if task_type == 'task':
                    canvas.append(parse(task))
                else:
                    canvas.append(parse(task[name]["tasks"]))

            except AttributeError:
                raise WorkflowSyntaxError()

        return canvas

    def parse_task(self, task, payload=None, single=True):
        name = list(task)[0]
        return self.new_task(
            task_name=name,
            queue=task[name].get("queue", ""),
            single=single,
            payload=payload
        )

    def parse_group(self, tasks):
        sub_canvas_tasks = [
            self.parse_task(t, single=False) for t in tasks]

        sub_canvas = group(*sub_canvas_tasks, task_id=uuid())
        self.previous = [s.id for s in sub_canvas_tasks]
        return sub_canvas

    def parse_dynamic_map(self, tasks):
        sub_canvas_tasks = []
        for var in self.workflow.payload:
            sub_canvas_tasks.append(chain(
                [self.parse_task(task=t, payload=[var]) for t in tasks],
                task_uid=uuid()))
        sub_canvas = group(*sub_canvas_tasks, task_id=uuid())
        self.previous = [s.id for s in sub_canvas_tasks]
        return sub_canvas

    def build(self):
        self.canvas = self.parse(self.tasks)
        self.canvas.insert(0, start.si(self.workflow.id).set(queue=self.queue))
        if self.sub_flows:
            self.canvas.append(sub_flows.s(self.sub_flows, self.workflow.id).set(queue=self.queue))
        self.canvas.append(end.si(self.workflow.id).set(queue=self.queue))

    def run(self):
        if not self.canvas:
            self.build()

        canvas = chain(*self.canvas, task_id=uuid())

        try:
            return canvas.apply_async()
        except Exception as e:
            self.workflow.status = StatusType.error
            self.workflow.save()
            raise e
