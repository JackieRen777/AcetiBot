const AXES = ['酸', '甜', '苦', '鲜', '咸']

const AXIS_ALIASES = {
  酸: ['酸', 'acid', 'sour'],
  甜: ['甜', 'sweet'],
  苦: ['苦', 'bitter'],
  鲜: ['鲜', 'umami'],
  咸: ['咸', 'salt', 'salty'],
}

function normalizeHeader(header) {
  return header.trim().toLowerCase().replaceAll('_', '').replaceAll(' ', '')
}

export function parseSensorCSV(text) {
  const trimmed = text.trim()
  if (!trimmed) return null

  const lines = trimmed.split(/\r?\n/)
  if (lines.length < 2) return null

  const headers = lines[0].split(',').map(normalizeHeader)
  const values = lines[1].split(',').map(value => Number.parseFloat(value.trim()))

  const parsed = AXES.map(axis => {
    const aliases = AXIS_ALIASES[axis]
    const index = headers.findIndex(header => aliases.some(alias => header.includes(alias)))
    const value = index >= 0 && Number.isFinite(values[index]) ? values[index] : 0
    return { axis, value }
  })

  return parsed.some(item => item.value > 0) ? parsed : null
}
