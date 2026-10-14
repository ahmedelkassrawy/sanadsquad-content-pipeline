import difflib
import hashlib
import re
from .agents import Agent, identity
from .config import load_type, local_file
from .contracts import AdvisorOutput, InstructionFile


def instruction_files(root,slots):
    names={"inputs/brand-voice.md"}
    for slot in slots:
        spec=load_type(root,slot.type)
        names.update([f"post_types/{slot.type}/writer.md",f"post_types/{slot.type}/{spec.rubric}"])
    return {name:InstructionFile(sha256=hashlib.sha256(local_file(root,name).read_bytes()).hexdigest(),text=local_file(root,name).read_text(encoding="utf-8")) for name in sorted(names)}


def advice_patch(request,output):
    identity(request.slot_id,request.attempt,output)
    texts={name:file.text for name,file in request.instructions.items()}
    changed=set()
    for edit in output.edits:
        if edit.path not in texts or edit.source_sha256!=request.instructions[edit.path].sha256:
            raise ValueError("invalid advice source")
        text=texts[edit.path]
        if text.count(edit.before)!=1: raise ValueError("ambiguous advice replacement")
        if text.startswith("---\n") and edit.before in text.split("---",2)[1]:
            raise ValueError("advice cannot edit machine rules")
        candidate=text.replace(edit.before,edit.after,1)
        if candidate.startswith("---\n") and candidate.split("---",2)[1]!=text.split("---",2)[1]:
            raise ValueError("advice cannot edit machine rules")
        if set(re.findall(r"\d+(?:\.\d+)?",edit.after))-set(re.findall(r"\d+(?:\.\d+)?",text)):
            raise ValueError("advice introduces numeric claims")
        texts[edit.path]=candidate
        changed.add(edit.path)
    patch="".join("".join(difflib.unified_diff(request.instructions[name].text.splitlines(keepends=True),texts[name].splitlines(keepends=True),fromfile="a/"+name,tofile="b/"+name)) for name in sorted(changed))
    if len(patch)>16000: raise ValueError("advice patch too large")
    return patch


class Advisor(Agent):
    def __init__(self,root,client): super().__init__(root,"advisor",client)

    async def __call__(self,request):
        output=await self.invoke(AdvisorOutput,request,request.slot_id,request.attempt)
        advice_patch(request,output)
        return output
