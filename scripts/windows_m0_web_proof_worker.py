"""Disposable M0 proof worker for real Flask startup; not a product launcher."""
import os,pathlib,sys
root,work=pathlib.Path(sys.argv[1]),pathlib.Path(sys.argv[2])
if os.environ.get('CWNG_M0_PROOF') != '1': raise RuntimeError('M0 proof mode is required')
sys.stdout=open(work/'web.log','a',encoding='utf-8',buffering=1);sys.stderr=sys.stdout
sys.path.insert(0,str(root));sys.path.insert(0,str(root/'scripts'))
os.add_dll_directory(os.environ['MAGICK_HOME']);os.add_dll_directory(os.environ['MAGICK_CODER_MODULE_PATH'])
sys.argv=['cps.py','-p',str(pathlib.Path(os.environ['CALIBRE_DBPATH'])/'app.db'),'-i','127.0.0.1','-m']
import cps
cps.updater_thread.start=lambda:None
from cps.main import register_blueprints
from windows_m0_readiness import readiness_middleware
import cps.schedule
cps.schedule.register_scheduled_tasks=lambda *_args,**_kwargs:None
cps.schedule.register_startup_tasks=lambda *_args,**_kwargs:None
app=cps.create_app()
if os.environ.get('M0_PROOF_PASSWORD'):
 from werkzeug.security import generate_password_hash
 admin=cps.ub.session.query(cps.ub.User).filter_by(name='admin').one()
 admin.password=generate_password_hash(os.environ['M0_PROOF_PASSWORD']);cps.ub.session.commit()
# Simulate completed location setup with the disposable library only.
cps.config.config_calibre_dir=os.environ['CWA_CALIBRE_LIBRARY_DIR']
if os.environ.get('M0_PROOF_KEPUBIFY'):
 cps.config.config_kepubifypath=os.environ['M0_PROOF_KEPUBIFY']
 cps.config.config_converterpath=''
cps.config.save()
from cps.calibre_init import init_calibre_db_from_config
assert init_calibre_db_from_config(cps.config,str(pathlib.Path(os.environ['CALIBRE_DBPATH'])/'app.db'))
cps.calibre_db.init_db()
register_blueprints(app)
traced = os.environ.get('CWNG_M0_TRACE_ORIGINS') != '1'
def ready():
 global traced
 if (work/'trace-refresh').exists():
  (work/'trace-refresh').unlink(); traced = False
 configured=pathlib.Path(cps.config.config_calibre_dir).resolve()
 if configured != pathlib.Path(os.environ['CWA_CALIBRE_LIBRARY_DIR']).resolve() or cps.calibre_db.session_factory is None: return False,''
 if not traced:
  # Trace the loaded runtime after the real Flask startup and request handlers.
  import ctypes,json
  modules={name:str(pathlib.Path(file).resolve()) for name,obj in list(sys.modules.items()) if (file:=getattr(obj,'__file__',None)) and file!='<stdin>'}
  python_roots=[root/'cps',root/'scripts',root/'.m0'/'python',pathlib.Path(os.environ['M0_RUNTIME']).resolve()]
  unexpected_python={name:path for name,path in modules.items() if not any(pathlib.Path(path).is_relative_to(base) for base in python_roots)}
  kernel=ctypes.WinDLL('kernel32',use_last_error=True);ps=ctypes.WinDLL('psapi',use_last_error=True)
  kernel.GetCurrentProcess.restype=ctypes.c_void_p
  ps.EnumProcessModulesEx.argtypes=[ctypes.c_void_p,ctypes.c_void_p,ctypes.c_ulong,ctypes.POINTER(ctypes.c_ulong),ctypes.c_ulong]
  ps.GetModuleFileNameExW.argtypes=[ctypes.c_void_p,ctypes.c_void_p,ctypes.c_wchar_p,ctypes.c_ulong];ps.GetModuleFileNameExW.restype=ctypes.c_ulong
  handles=(ctypes.c_void_p*4096)();needed=ctypes.c_ulong();proc=kernel.GetCurrentProcess()
  if not ps.EnumProcessModulesEx(proc,handles,ctypes.sizeof(handles),ctypes.byref(needed),3) or needed.value>ctypes.sizeof(handles): raise OSError('module enumeration failed or truncated')
  native=[]
  for handle in handles[:needed.value//ctypes.sizeof(ctypes.c_void_p)]:
   value=ctypes.create_unicode_buffer(32768)
   if not ps.GetModuleFileNameExW(proc,handle,value,len(value)): raise ctypes.WinError(ctypes.get_last_error())
   native.append(str(pathlib.Path(value.value).resolve()))
  native_roots=[root/'.m0'/'python',pathlib.Path(os.environ['M0_RUNTIME']).resolve(),pathlib.Path(os.environ['MAGICK_HOME']).resolve(),pathlib.Path(os.environ['SystemRoot']).resolve()]
  unexpected_native=[path for path in native if not any(pathlib.Path(path).is_relative_to(base) for base in native_roots)]
  (work/'web-origins.json').write_text(json.dumps({'python':modules,'native':native,'unexpected_python':unexpected_python,'unexpected_native':unexpected_native},indent=2),encoding='utf-8')
  if unexpected_python or unexpected_native: raise RuntimeError('web process loaded an unexpected module')
  traced = True
 stat=(configured/'metadata.db').stat()
 return (work/'setup-complete').exists(),f'{stat.st_dev}:{stat.st_ino}'
app.wsgi_app=readiness_middleware(app.wsgi_app,bytes.fromhex(os.environ['M0_READY_KEY']),ready)
cps.web_server.app=app
cps.web_server.start()
