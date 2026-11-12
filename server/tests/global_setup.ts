import { clean_test_environment } from './clean_environment.ts';

export default function setup(): () => void {
  return clean_test_environment();
}
