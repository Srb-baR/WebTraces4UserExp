"""Persistent shared API budget; uncertainty retains the full reserved amount."""
from contextlib import contextmanager
from decimal import Decimal,ROUND_CEILING
import json
import math
from pathlib import Path
import time
import uuid

class SpendingBudget:
    # Provider price ceilings in dollars per million tokens. No tools/request fees.
    PROMPT_MAX=.30
    COMPLETION_MAX=1.20

    def __init__(self,path,cap):
        if not math.isfinite(cap) or not 0<cap<=6:raise ValueError('Budget cap must be positive and <=$6')
        self.path=Path(path);self.cap=Decimal(str(cap));self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.locked() as ledger:pass

    @contextmanager
    def locked(self):
        import fcntl
        with Path(str(self.path)+'.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            ledger=json.loads(self.path.read_text()) if self.path.exists() else dict(cap_usd=str(self.cap),requests=[])
            if Decimal(str(ledger['cap_usd']))!=self.cap:raise ValueError('Ledger cap is fixed; do not reset or raise it when resuming')
            yield ledger
            temp=Path(str(self.path)+'.tmp');temp.write_text(json.dumps(ledger,indent=2)+'\n');temp.replace(self.path)

    @staticmethod
    def used(ledger):return sum((Decimal(str(r['retained_usd'])) for r in ledger['requests']),Decimal('0'))

    def reserve(self,messages,output,model,destination,digest):
        # This upper bound protects cost even in native-provider context-check mode.
        bound=sum(len(m['content'].encode('utf-8')) for m in messages)+4096
        amount=((Decimal(bound)*Decimal(str(self.PROMPT_MAX))+Decimal(output)*Decimal(str(self.COMPLETION_MAX)))/Decimal(1000000)*Decimal('1.05')).quantize(Decimal('.00000001'),rounding=ROUND_CEILING)
        with self.locked() as ledger:
            used=self.used(ledger)
            if used+amount>self.cap:raise SystemExit('BUDGET STOP: $%s used/reserved + $%s next-request ceiling exceeds shared $%s cap. No request sent.'%(used,amount,self.cap))
            identity=uuid.uuid4().hex
            ledger['requests'].append(dict(id=identity,model=model,destination=str(destination),prompt_hash=digest,input_token_bound=bound,max_output_tokens=output,reserved_usd=str(amount),retained_usd=str(amount),status='reserved',at=time.time()))
            print('BUDGET: $%s used/reserved; reserving $%s; shared cap $%s'%(used,amount,self.cap),flush=True)
        return identity

    def record(self,identity,cost):
        with self.locked() as ledger:
            row=next(r for r in ledger['requests'] if r['id']==identity)
            if isinstance(cost,(int,float)) and not isinstance(cost,bool) and math.isfinite(cost) and cost>=0:
                row.update(reported_cost_usd=cost,retained_usd=str(cost),status='provider_cost_reported')
            else:row['status']='cost_unknown_reservation_retained'
            print('BUDGET: shared usage/reservations $%s / $%s'%(self.used(ledger),self.cap),flush=True)
            if self.used(ledger)>self.cap:raise SystemExit('BUDGET STOP: provider reported more than reserved; inspect saved response. No further requests will be sent.')
