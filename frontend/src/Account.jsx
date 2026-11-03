import { useEffect, useState } from 'react';

export const PALETTES = [
 {id:'karasuno',name:'Карасуно',accent:'#ed731c',jersey:'#202124',trim:'#ed731c'},
 {id:'nekoma',name:'Некома',accent:'#c92737',jersey:'#c92737',trim:'#191919'},
 {id:'fukurodani',name:'Фукуродани',accent:'#c6be83',jersey:'#f4f3ed',trim:'#202020'},
 {id:'inarizaki',name:'Инаридзаки',accent:'#252826',jersey:'#252826',trim:'#ffffff'},
 {id:'aoba',name:'Аоба Дзёсай',accent:'#73c4b1',jersey:'#f4f6f5',trim:'#73c4b1'},
 {id:'shiratorizawa',name:'Сираторидзава',accent:'#834367',jersey:'#f8f6f7',trim:'#834367'},
];

async function api(path, options={}) {
 const response=await fetch(`/api/auth/${path}`,{...options,credentials:'same-origin',headers:{'X-Requested-With':'VolnaApp',...options.headers}});
 const data=response.status===204?null:await response.json();
 if(!response.ok)throw new Error(typeof data?.detail==='string'?data.detail:'Проверьте поля и повторите попытку');
 return data;
}

export default function Account({user,onUserChange}) {
 const [mode,setMode]=useState('login'),[username,setUsername]=useState(''),[password,setPassword]=useState('');
 const [busy,setBusy]=useState(false),[message,setMessage]=useState(''),[checking,setChecking]=useState(true);
 useEffect(()=>{
  let cancelled=false;
  fetch('/api/auth/me',{credentials:'same-origin'}).then(async response=>{
   if(response.status===401)return null;
   if(!response.ok)throw Error();
   return response.json();
  }).then(data=>{if(!cancelled)onUserChange(data)}).catch(()=>{if(!cancelled)setMessage('Сервис профиля недоступен. Попробуйте ещё раз немного позже.')}).finally(()=>{if(!cancelled)setChecking(false)});
  return()=>{cancelled=true};
 },[onUserChange]);
 async function submit(event){
  event.preventDefault();setBusy(true);setMessage('');
  try{const result=await api(mode,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username,password})});onUserChange(result);setPassword('');setMessage(mode==='register'?'Аккаунт создан':'Вы вошли в аккаунт')}
  catch(error){setMessage(error.message)}finally{setBusy(false)}
 }
 async function chooseTeam(team_id){
  setBusy(true);setMessage('');
  try{onUserChange(await api('profile',{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({team_id})}));setMessage('Команда и палитра сохранены')}
  catch(error){setMessage(error.message)}finally{setBusy(false)}
 }
 async function upload(event){
  const file=event.target.files?.[0];if(!file)return;
  if(file.size>5*1024*1024){setMessage('Выберите фото размером до 5 МБ');event.target.value='';return}
  setBusy(true);setMessage('');const body=new FormData();body.append('photo',file);
  try{onUserChange(await api('avatar',{method:'POST',body}));setMessage('Аватарка обновлена')}
  catch(error){setMessage(error.message)}finally{setBusy(false);event.target.value=''}
 }
 async function logout(){setBusy(true);setMessage('');try{await api('logout',{method:'POST'});onUserChange(null)}catch(error){setMessage(error.message)}finally{setBusy(false)}}
 const team=PALETTES.find(t=>t.id===user?.team_id)||PALETTES.find(t=>t.id==='fukurodani');
 return <section className="shell account-section" id="account"><div><p className="eyebrow dark">ЛИЧНЫЙ КАБИНЕТ</p><h2>{user?'Мой профиль':'Ближе к команде'}</h2><p className="muted">{user?'Выберите любимую команду — сайт примет её цвета в обеих темах.':'Создайте аккаунт, выберите аватарку и любимую команду.'}</p></div><div className="account-panel">{checking?<p role="status">Загрузка профиля…</p>:user?<><div className="profile-heading"><div className="profile-avatar">{user.avatar_url?<img src={user.avatar_url} alt="Ваша аватарка"/>:<span>{user.username[0].toUpperCase()}</span>}</div><div><h3>{user.username}</h3><p>{team.name}</p></div></div><label className="avatar-upload">Установить аватарку<input type="file" accept="image/jpeg,image/png,image/webp" disabled={busy} onChange={upload}/><span>JPG, PNG, WebP · до 5 МБ</span></label><div className="team-picker"><p>Моя команда</p><div className="team-options">{PALETTES.map(t=><button type="button" key={t.id} className={`team-option ${user.team_id===t.id?'selected':''}`} disabled={busy} aria-pressed={user.team_id===t.id} onClick={()=>chooseTeam(t.id)}><span className="team-kit" style={{'--kit-base':t.jersey,'--kit-trim':t.trim,'--kit-accent':t.accent}} aria-hidden="true"/><span>{t.name}</span><small>{user.team_id===t.id?'Выбрана':'Выбрать'}</small></button>)}</div><span className="input-hint">У каждой команды — светлая и тёмная палитра. Режим переключается в шапке.</span></div><div className="palette-preview" aria-label="Цвета выбранной команды"><span style={{background:team.accent}}/><span className="palette-white"/><span className="palette-black"/><p>Палитра: {team.name}</p></div><button className="theme-toggle" disabled={busy} onClick={logout}>Выйти из аккаунта</button></>:<><div className="account-tabs"><button type="button" className={mode==='login'?'active':''} onClick={()=>{setMode('login');setMessage('')}}>Вход</button><button type="button" className={mode==='register'?'active':''} onClick={()=>{setMode('register');setMessage('')}}>Регистрация</button></div><form className="auth-form" onSubmit={submit}><label>Логин<input required minLength={3} maxLength={40} pattern="[A-Za-z0-9_]+" autoComplete="username" value={username} onChange={e=>setUsername(e.target.value)} placeholder="Например, volley_fan"/><span className="input-hint">От 3 символов: латинские буквы, цифры, _</span></label><label>Пароль<input required minLength={8} maxLength={128} type="password" autoComplete={mode==='login'?'current-password':'new-password'} value={password} onChange={e=>setPassword(e.target.value)}/><span className="input-hint">Не менее 8 символов</span></label><button className="primary-button" disabled={busy}>{busy?'Подождите…':mode==='login'?'Войти':'Создать аккаунт'}</button></form></>}{message&&<p className="account-message" role="status">{message}</p>}</div></section>
}
