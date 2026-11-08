// Container entrypoint: exits if staging auth/persistent volume is not configured.
import { spawn } from 'node:child_process';
import { readFile, stat } from 'node:fs/promises';
import { createGateway } from './preview-gateway.mjs';
import path from 'node:path';
import process from 'node:process';
const cwd=process.cwd();
const dataDir=process.env.JEV_DATA_DIR||'/data';
if(process.env.NODE_ENV!=='production')throw new Error('PRODUCTION_BUILD_REQUIRED');
if(process.env.ENABLE_REAL_TRADING!=='false')throw new Error('ENABLE_REAL_TRADING_MUST_BE_FALSE');
if(!process.env.JEV_PREVIEW_USER||!process.env.JEV_PREVIEW_PASSWORD)throw new Error('PREVIEW_CREDENTIALS_REQUIRED');
if(process.env.JEV_PREVIEW_PASSWORD.length<20)throw new Error('PREVIEW_PASSWORD_TOO_SHORT');
const mountinfo=await readFile('/proc/self/mountinfo','utf8');
if(!mountinfo.split('\n').some(line=>line.includes(` ${dataDir.replaceAll(' ','\\040')} `)))throw new Error('PERSISTENT_VOLUME_NOT_MOUNTED');
await stat(path.join(cwd,'dist','trading.html'));
await stat(path.join(cwd,'server','trading_api.mjs'));
const adapter=spawn(process.execPath,['--experimental-sqlite','--no-warnings','server/trading_api.mjs'],{
 cwd,env:{...process.env,HOST:'127.0.0.1',PORT:'4791'},stdio:'inherit'
});
adapter.on('exit',code=>{console.error('paper adapter exited',code);process.exit(1)});
const port=Number(process.env.PORT||8080);
if(!Number.isInteger(port)||port<1||port>65535)throw new Error('INVALID_PORT');
const gateway=createGateway({staticDir:path.join(cwd,'dist'),previewUser:process.env.JEV_PREVIEW_USER,previewPassword:process.env.JEV_PREVIEW_PASSWORD,healthPath:process.env.JEV_ADAPTER_HEALTH_PATH||'/health'});
gateway.listen(port,'0.0.0.0',()=>console.log(`JEV staging gateway listening port ${port}; paper-only; auth enabled`));
for(const signal of ['SIGTERM','SIGINT'])process.on(signal,()=>{adapter.kill('SIGTERM');gateway.close(()=>process.exit(0));});
