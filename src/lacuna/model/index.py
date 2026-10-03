from __future__ import annotations
from pydantic import BaseModel, ConfigDict, Field
from lacuna.model.identity import ElementId

class MemberInfo(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: str
    static: bool = False
    final: bool = False
    synthetic: bool = False
    type_ref: str | None = None

class TypeInfo(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: str
    fields: tuple[str, ...] = ()
    methods: tuple[str, ...] = ()
    supertypes: tuple[str, ...] = ()
    imported_types: tuple[str, ...] = ()

class MethodInfo(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: str
    owner: str
    static: bool = False
    parameter_types: tuple[str, ...] = ()
    return_type: str | None = None
    thrown_types: tuple[str, ...] = ()

class ProgramIndex(BaseModel):
    model_config = ConfigDict(frozen=True)
    types: dict[str, TypeInfo] = Field(default_factory=dict)
    methods: dict[str, MethodInfo] = Field(default_factory=dict)
    members: dict[str, MemberInfo] = Field(default_factory=dict)

    def type_of(self, element: str) -> str:
        return str(ElementId(element).declaring_type)

    def transitive_supertypes(self, type_id: str) -> list[str]:
        out: list[str] = []
        seen = {type_id}
        stack = [type_id]
        while stack:
            cur = stack.pop()
            info = self.types.get(cur)
            if info is None:
                continue
            for sup in info.supertypes:
                if sup not in seen:
                    seen.add(sup)
                    out.append(sup)
                    stack.append(sup)
        return sorted(out)

    def declared_state(self, type_id: str, *, include_inherited: bool=True) -> list[str]:
        out: set[str] = set()
        chain = [type_id] + (self.transitive_supertypes(type_id) if include_inherited else [])
        for t in chain:
            info = self.types.get(t)
            if info is not None:
                out.update(info.fields)
        return sorted(out)
