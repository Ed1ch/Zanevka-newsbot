// Work on grouped display lines only. History and export keep every physical line.
function visibleJournalLines(type, level, messages) {
  if (type !== 'header' || level !== 'INFO' || messages[0] !== 'Process lock получен') return messages;

  const details = messages.slice(1);
  if (details[0]?.trimStart() === 'Режим: первый проход, отсрочка разрешена') return [];
  // During a repeat pass retain its mode and the delayed.publish acknowledgement.
  return details.filter(line => line.trimStart() !==
    'Workflow: CHECK INBOX → DOWNLOAD BATCH → DOC+IMG MATCHING → VK PARSE → INTEGRITY CHECK → WORDPRESS PUBLISHING');
}
