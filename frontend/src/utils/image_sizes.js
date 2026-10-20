export const imageRatios = [
  { ratio: '1:1', size: '2048*2048' },
  { ratio: '3:4', size: '1536*2048' },
  { ratio: '4:3', size: '2048*1536' },
  { ratio: '16:9', size: '2048*1152' },
  { ratio: '9:16', size: '1152*2048' },
]
export const defaultImageSize = '1536*2048'

export function imageRatioOptions(model) {
  return imageRatios.map((option) => {
    if (!model) return option
    const [rw, rh] = option.ratio.split(':').map(Number)
    const size = (model.image_sizes || []).find((value) => {
      const [w, h] = value.split('*').map(Number)
      return w > 0 && h > 0 && w * rh === h * rw
    })
    return { ratio: option.ratio, size: size || option.size, disabled: !size }
  })
}

export function normalizeImageSize(size, options) {
  if (options.some((option) => !option.disabled && option.size === size)) return size
  const previous = imageRatios.find((option) => option.size === size)?.ratio
  return (
    options.find((option) => !option.disabled && option.ratio === previous)?.size ||
    options.find((option) => !option.disabled && option.ratio === '3:4')?.size ||
    options.find((option) => !option.disabled)?.size ||
    ''
  )
}
