import { useMemo } from 'react'
import { MockApiClient } from './model'
import { Workbench } from './components/Workbench'

export default function App() {
  const client = useMemo(() => new MockApiClient(), [])
  return <Workbench client={client} />
}
