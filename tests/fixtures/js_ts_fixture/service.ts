import { add } from './math'
import type { Config } from './types'
export interface Greeter { greet(): string }
class Logger { log(message: string) { return message } }
export class UserService extends Logger implements Greeter {
  greet() { this.log('hello'); return add(1, 2) }
  static create() { return new UserService() }
  private #secret() { return true }
}
export enum Mode { Ready, Done }
export type UserId = string
