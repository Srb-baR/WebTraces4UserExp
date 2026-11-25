"""Actor-cluster statistics. All randomization is seeded; undefined values stay NaN."""
import itertools,warnings
import numpy as np
from scipy import stats
from statsmodels.stats.multitest import multipletests


def corr(x,y,method='spearman'):
 x=np.asarray(x,float);y=np.asarray(y,float);ok=np.isfinite(x)&np.isfinite(y);x=x[ok];y=y[ok]
 if len(x)<2 or np.ptp(x)==0 or np.ptp(y)==0:return np.nan,np.nan
 f={'spearman':stats.spearmanr,'pearson':stats.pearsonr,'kendall':stats.kendalltau}[method]
 r,p=f(x,y);return float(r),float(p)
def rowcorr(x,y):
 x=np.asarray(x,float);y=np.asarray(y,float);x=x-x.mean(-1,keepdims=True);y=y-y.mean(-1,keepdims=True)
 den=np.sqrt((x*x).sum(-1)*(y*y).sum(-1))
 return np.divide((x*y).sum(-1),den,out=np.full(den.shape,np.nan),where=den>1e-14)
def rowrho(x,y):return rowcorr(stats.rankdata(x,axis=-1),stats.rankdata(y,axis=-1))
def interval(values):
 v=np.asarray(values,float);v=v[np.isfinite(v)]
 return (*np.quantile(v,[.025,.975]),len(v)) if len(v) and len(v)>=.95*np.size(values) else (np.nan,np.nan,len(v))
def ci_fields(prefix,values):
 lo,hi,n=interval(values);return {prefix+'_ci_low':lo,prefix+'_ci_high':hi,prefix+'_valid_resamples':n}
def centered_p(samples,observed,alternative='two-sided'):
 """Null-centered cluster bootstrap test, NOT the fraction of raw draws crossing 0.
 Asymptotic plug-in null for a difference statistic. No randomization/causal claim.
 """
 v=np.asarray(samples);v=v[np.isfinite(v)]
 if not np.isfinite(observed) or not len(v) or len(v)<.95*np.size(samples):return np.nan
 if np.std(v)<1e-14:return np.nan
 errors=v-observed
 count=np.sum(errors>=observed) if alternative=='greater' else np.sum(np.abs(errors)>=abs(observed))
 return (1+count)/(len(v)+1)
def classic(x,y,weights=None):
 x=np.asarray(x,float);y=np.asarray(y,float);ok=np.isfinite(x)&np.isfinite(y);x=x[ok];y=y[ok]
 if not len(x):return {}
 w=np.ones(len(x)) if weights is None else np.asarray(weights)[ok];w=w/w.sum();d=y-x
 rho=corr(x,y)[0];tau=corr(x,y,'kendall')[0]
 if weights is not None and np.ptp(w)>1e-14:
  def ranks(values):
   unique,inv=np.unique(values,return_inverse=True);mass=np.bincount(inv,weights=w);mid=np.cumsum(mass)-mass/2;return mid[inv]
  a=ranks(x);b=ranks(y);a-=np.sum(w*a);b-=np.sum(w*b);den=np.sqrt(np.sum(w*a*a)*np.sum(w*b*b));rho=np.sum(w*a*b)/den if den>0 else np.nan
  sx=np.sign(x[:,None]-x[None,:]);sy=np.sign(y[:,None]-y[None,:]);pairw=w[:,None]*w[None,:];den=np.sqrt(np.sum(pairw*(sx!=0))*np.sum(pairw*(sy!=0)));tau=np.sum(pairw*sx*sy)/den if den>0 else np.nan
 return dict(exact=np.sum(w*(d==0)),near=np.sum(w*(abs(d)<=1)),mae=np.sum(w*abs(d)),rmse=np.sqrt(np.sum(w*d*d)),bias=np.sum(w*d),rho=rho,tau_b=tau)
def population(x,y):
 x=np.asarray(x,float);y=np.asarray(y,float);x=x[np.isfinite(x)];y=y[np.isfinite(y)]
 if not len(x) or not len(y):return {}
 p=np.bincount(x.astype(int),minlength=6)[1:6]/len(x);q=np.bincount(y.astype(int),minlength=6)[1:6]/len(y)
 delta=np.arange(1,6)[None,:]-np.arange(1,6)[:,None];cross=p[:,None]*q[None,:]
 return dict(mean_actor=x.mean(),sd_actor=x.std(ddof=1),mean_evaluator=y.mean(),sd_evaluator=y.std(ddof=1),bias=y.mean()-x.mean(),overlap=np.minimum(p,q).sum(),wasserstein=np.abs(np.cumsum(p)[:4]-np.cumsum(q)[:4]).sum(),population_exact=np.sum(cross*(delta==0)),population_near=np.sum(cross*(abs(delta)<=1)),population_mae=np.sum(cross*abs(delta)),population_rmse=np.sqrt(np.sum(cross*delta**2)),actor_distribution=p.tolist(),evaluator_distribution=q.tolist())
def weighted_rho(c):
 """Spearman from (possibly fractional) weighted 5x5 contingency matrices."""
 c=np.asarray(c,float);n=c.sum((-2,-1));a=c.sum(-1);b=c.sum(-2)
 ra=np.cumsum(a,-1)-a/2;rb=np.cumsum(b,-1)-b/2
 ma=(ra*a).sum(-1)/n;mb=(rb*b).sum(-1)/n
 cov=(c*(ra-ma[...,None])[..., :,None]*(rb-mb[...,None])[...,None,:]).sum((-2,-1))
 va=(a*(ra-ma[...,None])**2).sum(-1);vb=(b*(rb-mb[...,None])**2).sum(-1);den=np.sqrt(va*vb)
 return np.divide(cov,den,out=np.full(np.shape(den),np.nan),where=den>1e-14)

def clustered_metrics(frame,B,rng):
 groups=list(frame.groupby('actor_id',sort=True));n=len(groups)
 x=np.array([g.original_actor_score.iloc[0] for _,g in groups]);ys=[g.evaluator_score.to_numpy(float) for _,g in groups]
 hist=np.zeros((n,5,5));cnt=np.array([len(v) for v in ys]);means=np.array([v.mean() for v in ys])
 for i,(a,v) in enumerate(zip(x,ys)):
  for b in v:hist[i,int(a)-1,int(b)-1]+=1
 # Cluster sums retain every repeated rating; actor-balanced marginal statistics use hist/k_i.
 balanced=hist/cnt[:,None,None];counts=rng.multinomial(n,np.full(n,1/n),size=B)
 boot=np.einsum('bi,ijk->bjk',counts,hist,optimize=True)
 boot_bal=np.einsum('bi,ijk->bjk',counts,balanced,optimize=True)/n
 delta=np.arange(1,6)[None,:]-np.arange(1,6)[:,None]
 sums=hist.sum(0);probs=sums/sums.sum();primary=balanced.mean(0)
 out=dict(n_actors=n,n_responses=int(cnt.sum()),rho=float(weighted_rho(sums)),rho_actor_mean=corr(x,means)[0],rho_actor_balanced=float(weighted_rho(primary)))
 out.update(ci_fields('rho',weighted_rho(boot)))
 out.update(ci_fields('rho_actor_balanced',weighted_rho(boot_bal)))
 idx=rng.integers(0,n,size=(B,n));out.update(ci_fields('rho_actor_mean',rowrho(x[idx],means[idx])))
 metrics={'exact':delta==0,'near':abs(delta)<=1,'mae':abs(delta),'mse':delta**2,'bias':delta}
 for key,cost in metrics.items():
  point=float(np.sum(primary*cost));v=np.sum(boot_bal*cost,axis=(1,2))
  out[key if key!='mse' else 'rmse']=point if key!='mse' else np.sqrt(point)
  out.update(ci_fields(key if key!='mse' else 'rmse',v if key!='mse' else np.sqrt(v)))
  out['response_weighted_'+key]=float(np.sum(probs*cost))
 # Actor-level permutation moves the original score labels; predictions stay clustered.
 permutations=np.array([rng.permutation(n) for _ in range(B)])
 xp=x[permutations].astype(int)-1
 near_cost=np.array([[np.mean(abs(v-k)<=1) for k in range(1,6)] for v in ys]);mae_cost=np.array([[np.mean(abs(v-k)) for k in range(1,6)] for v in ys])
 shuffled_near=near_cost[np.arange(n)[None,:],xp].mean(1);shuffled_mae=mae_cost[np.arange(n)[None,:],xp].mean(1)
 out.update(shuffled_near=shuffled_near.mean(),ig_near=out['near']-shuffled_near.mean(),ig_near_p=(1+np.sum(shuffled_near>=out['near']-1e-12))/(B+1),shuffled_mae=shuffled_mae.mean(),ig_mae=shuffled_mae.mean()-out['mae'],ig_mae_p=(1+np.sum(shuffled_mae<=out['mae']+1e-12))/(B+1),permutations=B)
 return out

def icc(frame):
 groups=[g.evaluator_score.to_numpy(float) for _,g in frame.groupby('actor_id')];n=len(groups);k=np.array([len(v) for v in groups]);N=k.sum()
 if n<2 or N<=n:return dict(icc_1_1=np.nan,icc_1_k=np.nan,reason='Fewer than two actors or no repeated evaluations')
 allv=np.concatenate(groups);grand=allv.mean();means=np.array([v.mean() for v in groups]);msb=np.sum(k*(means-grand)**2)/(n-1);msw=sum(np.sum((v-v.mean())**2) for v in groups)/(N-n)
 k0=(N-np.sum(k*k)/N)/(n-1);vb=(msb-msw)/k0;vw=msw;kh=n/np.sum(1/k)
 single=vb/(vb+vw) if vb+vw!=0 else np.nan;average=vb/(vb+vw/kh) if vb+vw/kh!=0 else np.nan
 return dict(icc_1_1=single,icc_1_k=average,between_actor_variance=vb,within_actor_variance=vw,ms_between=msb,ms_within=msw,k_effective_anova=k0,k_harmonic=kh,k_min=k.min(),k_max=k.max(),n_actors=n,n_responses=int(N),sqrt_icc_heuristic=np.sqrt(single) if single>=0 else np.nan,method='Balanced one-way ANOVA' if np.ptp(k)==0 else 'Unbalanced one-way ANOVA method of moments; k0 correction; ICC average uses harmonic k; descriptive approximation')

PERMS=np.array(list(itertools.permutations(range(6))))
UP=np.triu_indices(6,1)
def matrix(v):
 v=np.asarray(v,float)
 if v.ndim!=2 or v.shape[0]<3:return np.full((6,6),np.nan)
 ranked=stats.rankdata(v,axis=0)
 with np.errstate(invalid='ignore',divide='ignore'):return np.corrcoef(ranked,rowvar=False)
def fidelity(a,b):
 x=a[UP];y=b[UP]
 # Six-dimensional fidelity needs all 15 edges, not a hand-picked finite subset.
 if not np.isfinite(x).all() or not np.isfinite(y).all():return np.nan
 return corr(x,y,'pearson')[0]
def structural(x,y):
 a=matrix(x);b=matrix(y);r=fidelity(a,b)
 if not np.isfinite(r):return dict(structural_r=np.nan,mantel_p=np.nan,mantel_permutations=720,reason='Undefined full 15-edge correlation vector (constant dimension or insufficient complete vectors)'),a,b
 vals=np.array([fidelity(a,b[p][:,p]) for p in PERMS]);p=np.mean(vals>=r-1e-12)
 return dict(structural_r=r,mantel_p=p,mantel_permutations=720,reason=''),a,b

def boot_matrix(v,idx):
 """Batched rank correlation matrices; re-rank within each resample for ties."""
 rank=stats.rankdata(np.asarray(v)[idx],axis=1);rank-=rank.mean(1,keepdims=True)
 cross=np.einsum('bni,bnj->bij',rank,rank);sd=np.sqrt(np.diagonal(cross,axis1=1,axis2=2));den=sd[:,:,None]*sd[:,None,:]
 return np.divide(cross,den,out=np.full(cross.shape,np.nan),where=den>1e-14)
def batch_fidelity(a,b):
 aa=a[:,UP[0],UP[1]];bb=b[:,UP[0],UP[1]]
 return rowcorr(aa,bb)
def fdr(frame,column,family):
 frame[column.replace('_p','_q') if column.endswith('_p') else column+'_q']=np.nan
 qcol=column.replace('_p','_q') if column.endswith('_p') else column+'_q'
 groups=frame.groupby(family,dropna=False).groups.values() if family else [frame.index]
 for indices in groups:
  ids=[i for i in indices if np.isfinite(frame.loc[i,column])]
  if ids:frame.loc[ids,qcol]=multipletests(frame.loc[ids,column],method='fdr_bh')[1]
 return frame

def weighted_matrix(v,w):
 """Tie-corrected Spearman matrix for an equal-model weighted Likert mixture."""
 v=np.asarray(v,float);w=np.asarray(w,float);w=w/w.sum();rank=np.zeros_like(v)
 for j in range(v.shape[1]):
  ps=np.array([w[v[:,j]==k].sum() for k in range(1,6)]);mid=np.cumsum(ps)-ps/2;rank[:,j]=mid[v[:,j].astype(int)-1]
 rank-=np.sum(rank*w[:,None],axis=0);cov=(rank*w[:,None]).T@rank;sd=np.sqrt(np.diag(cov));den=sd[:,None]*sd[None,:]
 return np.divide(cov,den,out=np.full(cov.shape,np.nan),where=den>1e-14)

def contrast_ci_fields(observed,values):
 """Basic bootstrap interval inverts the same centered-error pivot as the test.
 Percentile endpoints are retained as a finite-sample sensitivity analysis.
 """
 lo,hi,n=interval(values)
 return dict(delta_ci_low=2*observed-hi,delta_ci_high=2*observed-lo,delta_valid_resamples=n,delta_percentile_ci_low=lo,delta_percentile_ci_high=hi,delta_ci_method='basic/reverse-percentile; null-centered error pivot')
