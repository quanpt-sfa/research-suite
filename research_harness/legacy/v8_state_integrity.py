from __future__ import annotations
from contextlib import contextmanager
from pathlib import Path
import csv, hashlib, json, os, tempfile, time

GATE_LEDGER_BASE_FIELDS=['gate_event_id','gate_id','phase','timestamp','input_fingerprint','verdict','reason','suite_version','reviewer','notes']
ARTIFACT_REGISTRY_BASE_FIELDS=['artifact_id','artifact_type','path','schema_version','created_by_phase','created_by_suite','input_artifact_ids','sha256','status','supersedes','notes']


def canonical_json_hash(obj) -> str:
    raw=json.dumps(obj,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()


def state_fingerprint(obj:dict)->str:
    clean={k:v for k,v in obj.items() if k!='state_fingerprint'}
    return canonical_json_hash(clean)


def stamp_state(obj:dict)->dict:
    out=dict(obj)
    out['state_fingerprint']=state_fingerprint(out)
    return out


def verify_state(obj:dict)->bool:
    expected=str(obj.get('state_fingerprint') or '')
    return bool(expected) and expected==state_fingerprint(obj)


def atomic_write_text(path:Path,text:str,encoding='utf-8'):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(prefix='.'+path.name+'.',suffix='.tmp',dir=str(path.parent))
    try:
        with os.fdopen(fd,'w',encoding=encoding,newline='') as f:
            f.write(text); f.flush(); os.fsync(f.fileno())
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp):
            try: os.unlink(tmp)
            except OSError: pass


def atomic_write_json(path:Path,obj:dict):
    atomic_write_text(path,json.dumps(obj,indent=2,ensure_ascii=False),encoding='utf-8')


@contextmanager
def project_file_lock(lock_path:Path,timeout_seconds:float=30.0):
    """Cross-platform advisory lock around project-state mutation.

    fcntl is used on POSIX, msvcrt on Windows. The file stays in the project
    control directory so two suite processes coordinate on the same lock.
    """
    lock_path=Path(lock_path); lock_path.parent.mkdir(parents=True,exist_ok=True)
    fh=lock_path.open('a+b')
    start=time.monotonic()
    try:
        if os.name=='nt':
            import msvcrt
            while True:
                try:
                    fh.seek(0); msvcrt.locking(fh.fileno(),msvcrt.LK_NBLCK,1); break
                except OSError:
                    if time.monotonic()-start>=timeout_seconds: raise TimeoutError(f'PROJECT_STATE_LOCK_TIMEOUT:{lock_path}')
                    time.sleep(0.05)
        else:
            import fcntl
            while True:
                try:
                    fcntl.flock(fh.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB); break
                except BlockingIOError:
                    if time.monotonic()-start>=timeout_seconds: raise TimeoutError(f'PROJECT_STATE_LOCK_TIMEOUT:{lock_path}')
                    time.sleep(0.05)
        yield
    finally:
        try:
            if os.name=='nt':
                import msvcrt
                fh.seek(0); msvcrt.locking(fh.fileno(),msvcrt.LK_UNLCK,1)
            else:
                import fcntl
                fcntl.flock(fh.fileno(),fcntl.LOCK_UN)
        finally:
            fh.close()


def _csv_row_hash(fieldnames,row,prev):
    payload={k:str(row.get(k,'') or '') for k in fieldnames if k not in {'prev_row_sha256','row_sha256'}}
    payload['prev_row_sha256']=prev
    return canonical_json_hash(payload)


def _read_csv_matrix(path:Path):
    """Read a CSV as a physical matrix so malformed width/header rows are visible.

    DictReader is convenient for semantic access but can silently place surplus cells
    under a ``None`` key.  Integrity ledgers must fail closed instead of hashing only
    the recognized prefix of a malformed row.
    """
    with Path(path).open(encoding='utf-8',newline='') as f:
        return list(csv.reader(f))


def _csv_physical_findings(path:Path,expected_header:list[str]|None=None):
    path=Path(path); out=[]
    if not path.exists() or not path.stat().st_size:
        return out
    matrix=_read_csv_matrix(path)
    if not matrix or not matrix[0]:
        return [{'severity':'HARD','rule':'LEDGER_HEADER_MISSING_OR_EMPTY','path':str(path)}]
    header=list(matrix[0])
    if expected_header is not None and header!=list(expected_header):
        out.append({'severity':'HARD','rule':'LEDGER_SCHEMA_MISMATCH','path':str(path),'expected':list(expected_header),'actual':header})
    for row_no,cells in enumerate(matrix[1:],2):
        if not cells:
            out.append({'severity':'HARD','rule':'LEDGER_BLANK_PHYSICAL_ROW','path':str(path),'row':row_no})
            continue
        if cells==header:
            out.append({'severity':'HARD','rule':'DUPLICATE_HEADER_ROW','path':str(path),'row':row_no})
        if len(cells)!=len(header):
            out.append({'severity':'HARD','rule':'LEDGER_ROW_COLUMN_COUNT_MISMATCH','path':str(path),'row':row_no,'expected_columns':len(header),'actual_columns':len(cells)})
    return out


def migrate_hash_chained_csv_if_legacy(path:Path,base_fields:list[str]):
    """Atomically add chain fields to a clean legacy control CSV without appending a row."""
    fields=list(base_fields)
    for x in ['prev_row_sha256','row_sha256']:
        if x not in fields: fields.append(x)
    path=Path(path)
    if not path.exists() or not path.stat().st_size:
        return False
    physical=_csv_physical_findings(path)
    if physical:
        rules=','.join(sorted({x['rule'] for x in physical}))
        raise RuntimeError(f'CONTROL_LEDGER_PHYSICAL_INTEGRITY_FAILURE:{path}:{rules}')
    with path.open(encoding='utf-8',newline='') as f:
        reader=csv.DictReader(f); rr=list(reader); hdr=list(reader.fieldnames or [])
    if hdr==fields:
        prior=verify_hash_chained_csv(path)
        if prior:
            rules=','.join(sorted({x['rule'] for x in prior}))
            raise RuntimeError(f'CONTROL_LEDGER_PREEXISTING_INTEGRITY_FAILURE:{path}:{rules}')
        return False
    if hdr!=list(base_fields):
        raise RuntimeError(f'CONTROL_LEDGER_SCHEMA_MISMATCH:{path}:{hdr}')
    prev='GENESIS'; migrated=[]
    for old in rr:
        wr={k:old.get(k,'') for k in fields}; wr['prev_row_sha256']=prev
        wr['row_sha256']=_csv_row_hash(fields,wr,prev); prev=wr['row_sha256']; migrated.append(wr)
    fd,tmp=tempfile.mkstemp(prefix='.'+path.name+'.',suffix='.tmp',dir=str(path.parent)); os.close(fd)
    try:
        with open(tmp,'w',encoding='utf-8',newline='') as f:
            w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows(migrated); f.flush(); os.fsync(f.fileno())
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)
    return True


def append_hash_chained_csv(path:Path,base_fields:list[str],row:dict):
    """Append one row to a hash-chained CSV, failing closed on prior corruption.

    A non-empty file is considered to already own its header even when it has zero
    data rows.  This distinction prevents the historical header-only-template bug in
    which the first append emitted a second header.  Existing chained ledgers are
    verified before mutation; a writer must never extend a corrupted chain.
    """
    fields=list(base_fields)
    for x in ['prev_row_sha256','row_sha256']:
        if x not in fields: fields.append(x)
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    existing=[]; header_present=False
    if path.exists() and path.stat().st_size:
        matrix=_read_csv_matrix(path)
        if not matrix or not matrix[0]:
            raise RuntimeError(f'CONTROL_LEDGER_HEADER_MISSING_OR_EMPTY:{path}')
        hdr=list(matrix[0]); header_present=True
        # Physical-shape checks must precede DictReader/migration.  Otherwise a
        # repeated header can masquerade as a scientific/control data record and
        # surplus cells can be silently ignored by DictReader.
        physical=_csv_physical_findings(path)
        if physical:
            rules=','.join(sorted({x['rule'] for x in physical}))
            raise RuntimeError(f'CONTROL_LEDGER_PHYSICAL_INTEGRITY_FAILURE:{path}:{rules}')
        with path.open(encoding='utf-8',newline='') as f:
            reader=csv.DictReader(f); rr=list(reader); hdr=list(reader.fieldnames or [])
        # Non-destructive additive migration from the pre-chain control schema.
        if hdr==base_fields:
            prev='GENESIS'; migrated=[]
            for old in rr:
                wr={k:old.get(k,'') for k in fields}; wr['prev_row_sha256']=prev
                wr['row_sha256']=_csv_row_hash(fields,wr,prev); prev=wr['row_sha256']; migrated.append(wr)
            fd,tmp=tempfile.mkstemp(prefix='.'+path.name+'.',suffix='.tmp',dir=str(path.parent)); os.close(fd)
            try:
                with open(tmp,'w',encoding='utf-8',newline='') as f:
                    w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows(migrated); f.flush(); os.fsync(f.fileno())
                os.replace(tmp,path)
            finally:
                if os.path.exists(tmp): os.unlink(tmp)
            rr=migrated; hdr=fields; header_present=True
        if list(hdr)!=fields:
            raise RuntimeError(f'CONTROL_LEDGER_SCHEMA_MISMATCH:{path}:{list(hdr)}')
        # Existing chained state must be valid before it is extended.  This converts
        # append behavior from "preserve corruption and continue" to fail-closed.
        prior_findings=verify_hash_chained_csv(path)
        if prior_findings:
            rules=','.join(sorted({x['rule'] for x in prior_findings}))
            raise RuntimeError(f'CONTROL_LEDGER_PREEXISTING_INTEGRITY_FAILURE:{path}:{rules}')
        existing=rr
    prev=(existing[-1].get('row_sha256') or '') if existing else 'GENESIS'
    wr={k:row.get(k,'') for k in fields}
    wr['prev_row_sha256']=prev
    wr['row_sha256']=_csv_row_hash(fields,wr,prev)
    # Append is protected by caller lock. fsync to reduce torn history.
    with path.open('a',encoding='utf-8',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields)
        if not header_present: w.writeheader()
        w.writerow(wr); f.flush(); os.fsync(f.fileno())
    return wr['row_sha256']


def verify_hash_chained_csv(path:Path):
    path=Path(path); findings=[]
    if not path.exists() or not path.stat().st_size: return findings
    physical=_csv_physical_findings(path)
    findings.extend(physical)
    # Do not let malformed physical rows be normalized away by DictReader and then
    # reported as if only the cryptographic chain were at fault.
    if any(x['rule'] in {'LEDGER_HEADER_MISSING_OR_EMPTY','LEDGER_ROW_COLUMN_COUNT_MISMATCH'} for x in physical):
        return findings
    with path.open(encoding='utf-8',newline='') as f:
        reader=csv.DictReader(f); rr=list(reader); fields=list(reader.fieldnames or [])
    if not {'prev_row_sha256','row_sha256'} <= set(fields):
        findings.append({'severity':'HARD','rule':'LEDGER_HASH_CHAIN_FIELDS_MISSING','path':str(path)})
        return findings
    prev='GENESIS'
    for i,r in enumerate(rr,2):
        # A repeated header is a serialization record, not a scientific/control row.
        # It has already been reported explicitly; skip derivative hash noise.
        if [str(r.get(k,'') or '') for k in fields]==fields:
            continue
        if (r.get('prev_row_sha256') or '')!=prev:
            findings.append({'severity':'HARD','rule':'LEDGER_HASH_CHAIN_BROKEN','path':str(path),'row':i,'expected_prev':prev,'actual_prev':r.get('prev_row_sha256')})
        actual=_csv_row_hash(fields,r,(r.get('prev_row_sha256') or ''))
        if actual!=(r.get('row_sha256') or ''):
            findings.append({'severity':'HARD','rule':'LEDGER_ROW_HASH_MISMATCH','path':str(path),'row':i})
        prev=(r.get('row_sha256') or '')
    return findings


def append_hash_chained_jsonl(path:Path,event:dict):
    """Append a lineage event only when the pre-existing chain verifies cleanly."""
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    prev='GENESIS'
    if path.exists() and path.stat().st_size:
        prior=verify_hash_chained_jsonl(path)
        if prior:
            rules=','.join(sorted({x['rule'] for x in prior}))
            raise RuntimeError(f'LINEAGE_PREEXISTING_INTEGRITY_FAILURE:{path}:{rules}')
        lines=[x for x in path.read_text(encoding='utf-8').splitlines() if x.strip()]
        if lines:
            prev=json.loads(lines[-1]).get('event_sha256') or ''
            if not prev:
                raise RuntimeError(f'LINEAGE_LAST_EVENT_HASH_MISSING:{path}')
    obj=dict(event); obj['prev_event_sha256']=prev
    obj['event_sha256']=canonical_json_hash({k:v for k,v in obj.items() if k!='event_sha256'})
    with path.open('a',encoding='utf-8') as f:
        f.write(json.dumps(obj,ensure_ascii=False)+'\n'); f.flush(); os.fsync(f.fileno())
    return obj['event_sha256']


def verify_hash_chained_jsonl(path:Path):
    path=Path(path); out=[]
    if not path.exists() or not path.stat().st_size: return out
    prev='GENESIS'
    lines=path.read_text(encoding='utf-8').splitlines()
    for i,line in enumerate(lines,1):
        if not line.strip():
            out.append({'severity':'HARD','rule':'LINEAGE_BLANK_PHYSICAL_LINE','path':str(path),'row':i})
            continue
        try: obj=json.loads(line)
        except Exception as e:
            out.append({'severity':'HARD','rule':'LINEAGE_JSON_INVALID','path':str(path),'row':i,'error':str(e)}); continue
        if not isinstance(obj,dict):
            out.append({'severity':'HARD','rule':'LINEAGE_EVENT_NOT_OBJECT','path':str(path),'row':i}); continue
        if (obj.get('prev_event_sha256') or '')!=prev:
            out.append({'severity':'HARD','rule':'LINEAGE_HASH_CHAIN_BROKEN','path':str(path),'row':i})
        actual=canonical_json_hash({k:v for k,v in obj.items() if k!='event_sha256'})
        if actual!=(obj.get('event_sha256') or ''):
            out.append({'severity':'HARD','rule':'LINEAGE_EVENT_HASH_MISMATCH','path':str(path),'row':i})
        prev=(obj.get('event_sha256') or '')
    return out

# --- External project-state anchoring -------------------------------------------------
# Internal SHA-256 fingerprints/hash chains detect accidental or unanchored mutation.
# They are intentionally NOT claimed to resist a user who can rewrite the project and
# recompute every internal hash. A trusted checkpoint therefore lives outside the
# mutable project state, e.g. a Git commit containing integrity_anchor_payload.json.

def project_integrity_digest(hroot:Path)->str:
    hroot=Path(hroot).resolve()
    include_dirs=['00_master','01_finding','02_fitting','03_outlining','04_structuring','05_writing','control']
    excluded={
        'control/external_anchor_registry.csv',
        'control/integrity_anchor_payload.json',
        'control/integrity_policy.json',
        'control/.project_state.lock',
        'control/.state.lock',
    }
    files=[]
    for d in include_dirs:
        base=hroot/d
        if not base.exists(): continue
        for p in base.rglob('*'):
            if not p.is_file() or p.is_symlink(): continue
            rel=str(p.relative_to(hroot)).replace('\\','/')
            if rel in excluded or '/snapshots/' in rel or '__pycache__' in p.parts or p.suffix in {'.pyc','.pyo'}: continue
            files.append((rel,p))
    h=hashlib.sha256()
    for rel,p in sorted(files):
        ph=hashlib.sha256(p.read_bytes()).hexdigest()
        h.update(rel.encode('utf-8')); h.update(b'\0'); h.update(ph.encode('ascii')); h.update(b'\n')
    return h.hexdigest()


def make_integrity_anchor_payload(project_root:Path)->dict:
    project_root=Path(project_root).resolve(); hroot=project_root/'research_harness'
    # Digest and payload write belong to one project-state critical section.  The
    # lock file itself is excluded from the digest as a coordination artifact.
    with project_file_lock(hroot/'control'/'.state.lock'):
        obj={
            'schema':'research-harness-integrity-anchor-v1',
            'project_integrity_digest':project_integrity_digest(hroot),
            'created_at':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),
            'assurance':'EXTERNAL_CHECKPOINT_PAYLOAD; commit or record this outside mutable project state before relying on it',
        }
        atomic_write_json(hroot/'control'/'integrity_anchor_payload.json',obj)
        return obj


def verify_git_commit_anchor(project_root:Path,commit:str)->dict:
    import subprocess
    project_root=Path(project_root).resolve(); commit=(commit or '').strip()
    if not commit: return {'status':'FAIL','rule':'GIT_ANCHOR_COMMIT_MISSING'}
    try:
        repo=Path(subprocess.check_output(['git','-C',str(project_root),'rev-parse','--show-toplevel'],text=True,stderr=subprocess.STDOUT).strip()).resolve()
        project_rel=project_root.relative_to(repo)
        payload_rel=(project_rel/'research_harness'/'control'/'integrity_anchor_payload.json').as_posix()
        raw=subprocess.check_output(['git','-C',str(repo),'show',f'{commit}:{payload_rel}'],text=True,stderr=subprocess.STDOUT)
        payload=json.loads(raw)
    except Exception as e:
        return {'status':'FAIL','rule':'GIT_ANCHOR_UNRESOLVED','commit':commit,'error':str(e)}
    expected=str(payload.get('project_integrity_digest') or '')
    current=project_integrity_digest(project_root/'research_harness')
    return {
        'status':'PASS' if expected and expected==current else 'FAIL',
        'rule':'GIT_ANCHOR_MATCH' if expected and expected==current else 'GIT_ANCHOR_DIGEST_MISMATCH',
        'commit':commit,'payload_path':payload_rel,'anchored_digest':expected,'current_digest':current,
    }
