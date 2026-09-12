"""Static provider declarations and separately versioned capability observations."""
import time
from agent.protocol import CoreError


class ProviderCatalog:
    def __init__(self,journal): self.journal=journal

    def register(self,scope,id,*,endpoint,transport="chat_sse",authentication="none"):
        from urllib.parse import urlsplit
        url=urlsplit(endpoint)
        if url.scheme not in {"http","https"} or not url.hostname or url.username or url.password or url.fragment:
            raise CoreError("invalid_provider","Provider endpoint must be an HTTP(S) URL without embedded credentials")
        if transport!="chat_sse" or authentication not in {"none","bearer"}: raise CoreError("unsupported","Provider transport/auth mode unsupported")
        return self.journal.put("providers",id,scope,{"endpoint":endpoint,"transport":transport,"authentication":authentication})

    def observe(self,scope,id,*,model,context_limit,output_limit,tools=True,ttl=300,now=None):
        if self.journal.get("providers",id,scope) is None: raise CoreError("not_found","Unknown provider")
        if not model or type(context_limit) is not int or type(output_limit) is not int or not 0<output_limit<context_limit or ttl<=0:
            raise CoreError("invalid_provider","Invalid model limits")
        now=time.time() if now is None else now
        with self.journal.transaction():
            prior=self.journal.get("provider_capabilities",id,scope)
            return self.journal.put("provider_capabilities",id,scope,{"model":model,"context_limit":context_limit,"output_limit":output_limit,"tools":bool(tools),"observed_at":now,"expires_at":now+ttl},expected_revision=prior.revision if prior else 0)

    def resolve(self,scope,id,*,require_tools=False,now=None):
        now=time.time() if now is None else now
        static=self.journal.get("providers",id,scope)
        dynamic=self.journal.get("provider_capabilities",id,scope)
        if static is None or dynamic is None or dynamic.value["expires_at"]<=now: raise CoreError("provider_unavailable","Provider capability snapshot missing or stale")
        if require_tools and not dynamic.value["tools"]: raise CoreError("incompatible","Provider does not support tools")
        return {**static.value,**dynamic.value,"id":id,"revision":dynamic.revision,"static_revision":static.revision}
