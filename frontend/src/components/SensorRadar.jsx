import { RadarChart, Radar, PolarGrid, PolarAngleAxis, ResponsiveContainer, Tooltip } from 'recharts'

export default function SensorRadar({ data }) {
  return (
    <div className="my-3 bg-[#fafafa] border border-hairline rounded-xl p-4 w-72">
      <p className="text-xs text-muted mb-2 text-center">感官特征雷达图</p>
      <ResponsiveContainer width="100%" height={200}>
        <RadarChart data={data}>
          <PolarGrid stroke="#e7e5e4" />
          <PolarAngleAxis dataKey="axis" tick={{ fontSize: 12, fill: '#4e4e4e' }} />
          <Radar dataKey="value" stroke="#292524" fill="#292524" fillOpacity={0.15} />
          <Tooltip formatter={value => Number(value).toFixed(2)} />
        </RadarChart>
      </ResponsiveContainer>
    </div>
  )
}
