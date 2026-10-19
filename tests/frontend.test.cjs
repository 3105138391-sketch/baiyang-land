const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const script = fs.readFileSync('baiyang.html','utf8').match(/<script>([\s\S]*?)<\/script>/)[1];
function harness(fetchImpl) {
  class Node {
    constructor(){this.children=[];this.style={setProperty(){}};this.dataset={};this.classList={add(){},remove(){}};this.textContent='';}
    appendChild(n){this.children.push(n);n.parent=this;return n;}
    get firstChild(){return this.children[0];}
    set innerHTML(v){this.children=[];} get innerHTML(){return '';}
    addEventListener(name, fn){(this.events ||= {})[name]=fn;} setAttribute(){} focus(){} remove(){if(this.parent)this.parent.children=this.parent.children.filter(x=>x!==this);}
  }
  const nodes={}, storage=new Map();
  const document={getElementById:id=>nodes[id] ||= new Node(),createElement:()=>new Node(),body:new Node(),documentElement:new Node(),querySelectorAll:()=>[]};
  const context=vm.createContext({document,window:{addEventListener(){},confirm:()=>false,location:{reload(){}}},localStorage:{setItem:(k,v)=>storage.set(k,v),getItem:k=>storage.get(k)||null,removeItem:k=>storage.delete(k)},setTimeout:()=>1,clearTimeout(){},AbortController,TextEncoder,console,fetch:fetchImpl});
  vm.runInContext(script,context);
  return {run:code=>vm.runInContext(code,context),nodes,storage};
}
test('indented/fullwidth options are both parsed and stripped; battle fallback',()=>{
 const h=harness();
 assert.equal(h.run(`getOptions('  A. 前进\\n　Ｂ） 防御\\n c、观察').join('|')`),'前进|防御|观察');
 assert.equal(h.run(`stripReplyOptions('剧情\\n  A. 前进\\n　Ｂ） 防御')`),'剧情');
 assert.equal(h.run(`optionsForReply('⚔ 战斗开始').length`),4);
});
test('failure retry commits one action/reply; concurrent sends ignored; no prompt/image critical path',async()=>{
 let calls=0, resolve;
 const h=harness((url)=>{assert.equal(url,'/api/chat');calls++;if(calls===1)return Promise.reject(new Error('断线'));return new Promise(r=>resolve=r);});
 await h.run(`send('观察')`);
 assert.equal(h.run('history.length'),1);assert.equal(h.run('gameProgress.playerActions.length'),0);
 const retry=h.run('executePending()');await h.run(`send('重复')`);assert.equal(calls,2);
 resolve({ok:true,json:async()=>({choices:[{message:{content:'原地观察。\nA. 继续'}}]})});
 // Prevent first-turn image call, which is independently tested below.
 h.run('gameProgress.turns = 1'); await retry;
 assert.equal(h.run('history.length'),3);assert.equal(h.run('gameProgress.playerActions.length'),1);
 const saved=JSON.parse([...h.storage.values()][0]);assert.equal(saved.pendingTurn,null);assert.equal(saved.history[1].content,'原地观察。\nA. 继续');
 assert.equal(h.run('busy'),true);h.run('flushAll()');assert.equal(h.run('busy'),false);
});
test('pending and completed saves restore without replaying progress or fetching',()=>{
 const h=harness(()=>{throw new Error('must not fetch');});
 h.run(`history.push({role:'assistant',content:'⚔ 战斗开始'}); inBattle=true; lastOptions=optionsForReply(history[1].content); gameProgress.turns=4; pendingTurn='防御'; saveGame(); restoreGame(readSave());`);
 assert.equal(h.run('gameProgress.turns'),4);assert.equal(h.run('history.length'),2);assert.equal(h.run('pendingTurn'),'防御');assert.equal(h.run('lastOptions.length'),4);
 assert.equal(h.nodes['opts-area'].children.length,3);
});
test('corrupt storage and quota failure leave usable notice',()=>{
 const h=harness();h.run(`localStorage.getItem=()=>'{';`);assert.equal(h.run('readSave()'),null);
 h.run(`localStorage.setItem=()=>{throw new Error('quota')}; saveGame();`);
 assert.match(h.nodes['save-notice'].textContent,/存档暂不可用/);
});
test('image failure uses local scene while narrative can finish',async()=>{
 const h=harness(()=>Promise.reject(new Error('offline')));
 h.run(`history.push({role:'assistant', content:'📍 中传北门\\nA. 前进'}); busy=true; renderAssistantMessage(history[1].content, null); flushAll();`);
 assert.equal(h.run('busy'),false);
 const gm=h.nodes.output.children[0],slot=gm.children[1];assert.match(slot.firstChild.src,/assets\/scenes/);
 await Promise.resolve();
});

test('battle failure can retry or return to four original skill options',async()=>{
 const h=harness(()=>Promise.reject(new Error('断线')));
 h.run(`inBattle=true; lastOptions=optionsForReply('⚔ 战斗开始\\nA. 不完整'); showControls();`);
 assert.equal(h.nodes['opts-area'].children.length,4);
 assert.match(h.nodes['opts-area'].children[0].textContent,/风华斩/);
 await h.run(`send('风华斩')`);
 assert.equal(h.nodes['opts-area'].children[1].textContent,'重试本次行动');
 assert.equal(h.nodes['opts-area'].children[2].textContent,'返回原选项');
 h.nodes['opts-area'].children[2].events.click();
 assert.equal(h.nodes['opts-area'].children.length,4);
 assert.equal(h.nodes['opts-area'].style.display,'flex');
 assert.equal(h.nodes['opts-area'].children.every(n=>!n.disabled),true);
 assert.equal(h.run('pendingTurn'),null);
 assert.equal(h.run('gameProgress.playerActions.length'),0);
});
test('context respects character, UTF-8 and message budgets with complete recent pairs',()=>{
 const h=harness();
 const result=h.run(`(() => {
 const transcript=[{role:'system',content:'系统'.repeat(1000)}, {role:'assistant',content:'开场'}];
 for(let i=0;i<25;i++) transcript.push({role:'user',content:'行动'+i+'中'.repeat(1800)}, {role:'assistant',content:'结果'+i+'文'.repeat(2800)});
 return buildChatContext(transcript,'现在行动');
 })()`);
 assert.ok(result.length<=32);
 assert.ok(result.reduce((sum,m)=>sum+m.content.length,0)<=24000);
 assert.ok(Buffer.byteLength(JSON.stringify({model:'deepseek-chat',messages:result,temperature:.85,max_tokens:1500}))<=60*1024);
 assert.equal(result.at(-3).role,'user');assert.match(result.at(-3).content,/行动24/);
 const turns=result.slice(result[1].content==='开场'?2:1,-1);
 assert.equal(turns.length%2,0);turns.forEach((m,i)=>assert.equal(m.role,i%2?'assistant':'user'));
});
test('overlong actions receive feedback without request and Chinese IME Enter is ignored',async()=>{
 const h=harness(()=>{throw new Error('must not fetch');});
 await h.run(`send('长'.repeat(2001))`);
 assert.match(h.nodes['save-notice'].textContent,/2000/);assert.equal(h.run('pendingTurn'),null);
 h.nodes.input.value='输入中';h.nodes.input.events.keydown({key:'Enter',isComposing:true});
 assert.equal(h.nodes.input.value,'输入中');assert.equal(h.run('pendingTurn'),null);
});
