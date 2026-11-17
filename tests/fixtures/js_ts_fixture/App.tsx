import { double } from './utils.mjs'
import Button from './components/Button'
export function App() { return <Button onClick={() => double(2)} /> }
