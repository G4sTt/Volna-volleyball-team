import { useEffect, useState } from 'react';

export default function PlayerInfo({ player, canEdit, onSaved }) {
  const [text, setText] = useState(player.description || '');
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState('');
  useEffect(() => {
    setText(player.description || '');
  }, [player.id, player.description, canEdit]);

  if (!canEdit) {
    return <p className="player-description">{player.description || 'Информация об игроке пока не добавлена.'}</p>;
  }

  async function save(event) {
    event.preventDefault();
    setSaving(true);
    setMessage('');
    try {
      const response = await fetch(`/api/players/${player.id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'VolnaApp' },
        body: JSON.stringify({ description: text }),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : 'Не удалось сохранить информацию');
      onSaved(result);
      setMessage('Информация сохранена');
    } catch (error) {
      setMessage(error.message || 'Не удалось сохранить информацию');
    } finally {
      setSaving(false);
    }
  }

  return <form className="player-info-editor" onSubmit={save} onKeyDown={event => event.stopPropagation()}>
    <label>Об игроке
      <textarea value={text} maxLength={2000} rows={3} disabled={saving}
        placeholder="Расскажите об игроке: опыт, достижения, сильные стороны…"
        onChange={event => { setText(event.target.value); setMessage(''); }} />
    </label>
    <div className="player-info-actions">
      <small>{text.length} / 2000</small>
      <button className="primary-button" disabled={saving || text.trim() === (player.description || '')}>
        {saving ? 'Сохранение…' : 'Сохранить информацию'}
      </button>
    </div>
    {message && <p className="player-info-message" role="status">{message}</p>}
  </form>;
}
