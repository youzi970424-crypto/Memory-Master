const scenes=[
 {name:'会议纪要',icon:'todos',question:'下一轮用户访谈应该重点验证什么？',cards:[{title:'用户访谈 · 操作流程反馈',content:'客户更关心操作路径是否清晰，建议先观察核心流程的完成情况。',advice:'可以在下一轮访谈中观察用户完成核心操作的过程，记录卡点。'},{title:'需求评审 · 验证范围',content:'本轮先验证核心流程，小范围收集反馈，再决定是否扩大投入。',advice:'可以先用小范围访谈验证核心路径，再根据反馈调整范围。'}]},
 {name:'客户案例',icon:'cases',question:'新项目启动前，有哪些经验可以复用？',cards:[{title:'项目复盘 · 交接遗漏',content:'交接时缺少负责人和验收标准，导致同一问题被反复确认。',advice:'可以在启动时明确负责人、验收标准和交接清单。'},{title:'试点总结 · 分阶段验证',content:'把验证拆成两个阶段，有助于更早发现问题并调整方案。',advice:'可以把验证拆成阶段，在关键节点核对反馈并调整方案。'}]},
 {name:'知识沉淀',icon:'knowledge',question:'资源有限时，如何安排方案验证？',cards:[{title:'方案取舍 · 核心路径优先',content:'资源有限时先验证关键假设，避免在未经验证的方向上投入过多。',advice:'可以先列出关键假设，优先验证最影响方案成立的一项。'},{title:'工作方法 · 反馈记录',content:'记录使用场景、具体问题和影响，帮助区分个别反馈与共性需求。',advice:'可以按场景、问题和影响记录反馈，为后续投入提供依据。'}]}
];
scenes.unshift(scenes.splice(1,1)[0]);
scenes.splice(2,0,{name:'日常待办',icon:'meetings',question:'本周的验证工作应该如何推进？',cards:[{title:'整理访谈反馈',content:'按使用场景汇总访谈中的问题，整理需要进一步验证的假设。',advice:'可以先按场景汇总反馈，明确待验证的假设。'},{title:'准备试点计划',content:'明确本轮试点范围、参与者和反馈收集方式。',advice:'可以在开始前明确范围与参与者，并准备反馈记录表。'}]});
const tabsRoot=document.querySelector('.tabs');
tabsRoot.innerHTML=scenes.map((scene,i)=>`<button class="native-group" role="tab" aria-selected="false" aria-controls="memory-panel" id="tab-${i}" data-scene="${i}"><img src="assets/home-icon-${scene.icon}.png" alt=""><strong>${scene.name}</strong><span class="native-count">${scene.cards.length}条记忆</span><span class="native-arrow" aria-hidden="true"><img src="assets/card-open-arrow.svg" alt=""></span></button>`).join('');
const tabs=[...document.querySelectorAll('[data-scene]')];
let active=0;
let selected=new Set();
let run=0;
let busy=false;
let latestAnswer='';
const $=selector=>document.querySelector(selector);
const reduced=matchMedia('(prefers-reduced-motion: reduce)').matches;
function step(index){document.querySelectorAll('.demo-flow li').forEach((item,i)=>{item.classList.toggle('complete',i<index);if(i===index)item.setAttribute('aria-current','step');else item.removeAttribute('aria-current');});}
function renderCards(){const scene=scenes[active];$('#memory-grid').innerHTML=scene.cards.map((card,i)=>`<button class="demo-memory" aria-pressed="${selected.has(i)}" data-card="${i}"><span class="card-top"><img src="assets/home-icon-${scene.icon}.png" alt=""><span>${selected.has(i)?'✓ 已选择':'＋ 选择'}</span></span><strong>${card.title}</strong><span class="card-content">${card.content}</span></button>`).join('');$('#selected-memories').textContent=selected.size?`已选择 ${selected.size} 张记忆卡`:'选择想要参考的记忆，开始一次新的思考';$('#start-chat').disabled=!selected.size;}
function select(index){active=index;selected.clear();$('.native-fan-space').hidden=true;$('#memory-panel').hidden=false;$('.selection-action').hidden=false;tabs.forEach((tab,i)=>{tab.setAttribute('aria-selected',String(i===index));tab.tabIndex=i===index?0:-1;});$('#memory-panel').setAttribute('aria-labelledby',`tab-${index}`);renderCards();step(1);$('#back-groups').focus();}
function showGroups(){run++;busy=false;$('#memory-panel').hidden=true;$('.selection-action').hidden=true;$('#chat-demo').hidden=true;$('.native-fan-space').hidden=false;$('#saved-memory').hidden=true;$('#save-answer').hidden=true;step(0);}
tabs.forEach((tab,i)=>{tab.addEventListener('click',()=>select(i));tab.addEventListener('keydown',event=>{let next;if(event.key==='ArrowRight')next=(i+1)%tabs.length;else if(event.key==='ArrowLeft')next=(i+tabs.length-1)%tabs.length;else if(event.key==='Home')next=0;else if(event.key==='End')next=tabs.length-1;else return;event.preventDefault();tabs.forEach((t,j)=>t.tabIndex=j===next?0:-1);tabs[next].focus();});});
$('#memory-grid').addEventListener('click',event=>{const card=event.target.closest('[data-card]');if(!card)return;const i=Number(card.dataset.card);selected.has(i)?selected.delete(i):selected.add(i);renderCards();$(`[data-card="${i}"]`).focus();});
function scrollChat(){const box=$('#chat-scroll');box.scrollTo({top:box.scrollHeight,behavior:reduced?'instant':'smooth'});}
function message(text,kind){const item=document.createElement('div');item.className=`chat-message ${kind}`;item.textContent=text;$('#chat-messages').append(item);scrollChat();return item;}
const delay=ms=>new Promise(resolve=>setTimeout(resolve,reduced?0:ms));
async function reply(question,followup=false){if(busy)return;busy=true;const token=++run;$('#save-answer').hidden=true;$('#saved-memory').hidden=true;$('#send-chat').disabled=true;$('#chat-input').disabled=true;step(2);message(question,'user');$('#chat-status').textContent='正在结合选中的记忆整理示例回答…';await delay(650);if(token!==run)return;step(3);const cards=[...selected].map(i=>scenes[active].cards[i]);const parts=[followup?'这是预设体验，未调用模型。围绕刚才的问题，可以继续参考以下记忆：':'我会结合你选择的记忆，整理这次思考的起点。',...cards.map(card=>`「${card.title}」\n${card.content}\n${card.advice}`),'建议先明确本次目标，再验证关键假设，并记录实际反馈。具体安排还需结合这次的资源与约束确认。'];latestAnswer=cards.map(card=>card.advice).join('');for(const part of parts){const item=message('','assistant');if(reduced){item.textContent=part;}else{for(let i=0;i<part.length;i+=4){if(token!==run)return;item.textContent=part.slice(0,i+4);scrollChat();await delay(35);}}scrollChat();await delay(280);if(token!==run)return;}await delay(450);if(token!==run)return;busy=false;$('#chat-status').textContent='回答已完成，把值得复用的信息留下来。';$('#save-answer').hidden=false;scrollChat();$('#send-chat').disabled=false;$('#chat-input').disabled=false;step(4);}
$('#start-chat').addEventListener('click',()=>{if(!selected.size)return;$('#memory-panel').hidden=true;$('.selection-action').hidden=true;$('#chat-demo').hidden=false;$('#chat-messages').replaceChildren();$('#chat-input').value='';$('#restart-demo').focus();reply(scenes[active].question);});
$('#chat-form').addEventListener('submit',event=>{event.preventDefault();const question=$('#chat-input').value.trim();if(!question||busy)return;$('#chat-input').value='';reply(question,true);});
$('#save-answer').addEventListener('click',()=>{$('#saved-title').textContent=`${scenes[active].name} · 本次思考的收获`;$('#saved-content').textContent=latestAnswer;$('#saved-memory').hidden=false;$('#save-answer').hidden=true;scrollChat();$('#chat-status').textContent='已完成沉淀演示，未写入你的真实记忆。';});
$('#back-groups').addEventListener('click',()=>{showGroups();tabs[active].focus();});
$('#restart-demo').addEventListener('click',()=>{showGroups();tabs[active].focus();});
$('#add-memory').addEventListener('click',()=>$('#add-memory-dialog').showModal());
document.querySelectorAll('[data-download]').forEach(button=>button.addEventListener('click',()=>$('#download-dialog').showModal()));
showGroups();
if(reduced)document.querySelectorAll('video').forEach(video=>video.pause());
const fanSpace=$('.native-fan-space');
new ResizeObserver(()=>{if(fanSpace.clientWidth){const scale=fanSpace.clientWidth/908*0.88;fanSpace.style.setProperty('--fan-scale',scale);fanSpace.style.height=`${300*scale}px`;}}).observe(fanSpace);
