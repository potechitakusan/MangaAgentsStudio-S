'use strict';
// Outfit editor inside each character card (up to 10 outfits).
function wardrobeEditor(card,entry,i){
  const path=`characters.${i}`,outfits=entry.outfits||[],group=el('section',undefined,'wardrobe');
  group.append(el('h4',`衣装 ${outfits.length} / 10`),el('p','固定外見は身体・髪、衣装は服・靴などに分けます。既存の服入り外見は「制作AIで衣装を整理」で分離できます。','hint'));
  selectField(group,'既定の衣装',[['','先頭の衣装（未登録なら固定外見のみ）'],...outfits.map(o=>[o.id,o.name])],entry.default_outfit_id||'',path+'.default_outfit_id');
  outfits.forEach((outfit,j)=>{const box=el('details',undefined,'outfit-edit');box.append(el('summary',`${j+1}. ${outfit.name} · ${outfit.id}`));
    textField(box,'衣装名',outfit.name,`${path}.outfits.${j}.name`,1);textField(box,'衣装ID（英数字。コマの衣装指定に使います）',outfit.id,`${path}.outfits.${j}.id`,1);
    bilingualFields(box,'衣装',outfit.prompt,outfit.prompt_ja,`${path}.outfits.${j}.prompt`,`${path}.outfits.${j}.prompt_ja`);
    actionButton(box,'この衣装を削除',async()=>{const v=collectInputs();if(v.pages.some(p=>p.panels.some(c=>c.characters.some(a=>a.id===entry.id&&a.outfit_id===outfit.id))))throw new Error('使用中の衣装です。先にコマの衣装指定を変更してください');inputs=v;const c=v.characters[i];c.outfits.splice(j,1);if(c.default_outfit_id===outfit.id)c.default_outfit_id='';renderSettings();markDirty('衣装の削除: '+outfit.name);});group.append(box);
  });
  actionButton(group,'衣装を追加',async()=>{inputs=collectInputs();const c=inputs.characters[i];c.outfits??=[];if(c.outfits.length>=10)throw new Error('衣装は1人につき10種類までです');c.outfits.push({id:'outfit-'+crypto.randomUUID().slice(0,8),name:`衣装 ${c.outfits.length+1}`,prompt:'',prompt_ja:''});renderSettings();markDirty('衣装の追加: '+entry.name);const group=$('catalog-fields').querySelectorAll('.wardrobe')[i];group.closest('details').open=true;group.querySelector('.outfit-edit:last-of-type').open=true;});
  const instruction=textField(group,'制作AIへの衣装の指示（例：作業着と外出着の2種類）',inputs?.ui_inputs?.['wardrobe-'+entry.id]||'',null,2);instruction.dataset.wardrobeInstruction=entry.id;
  actionButton(group,'制作AIで衣装を整理・追加',async()=>{const request=instruction.value;await save();await runJob('wardrobe',{character_id:entry.id,instruction:request});});
  actionButton(group,'この人物を試し描き欄へ',async()=>{await save();$('trial-kind').value='character';trialTargets(entry.id);trialInvalidate();$('trial-controls').scrollIntoView({behavior:'smooth'});});card.append(group);
}

