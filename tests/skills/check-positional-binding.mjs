#!/usr/bin/env node
// Инвариант: анализирующий навык (*-info, *-validate, cfe-diff) не пишет в файл, который ему
// не назвали по имени. Позиционным остаётся только путь ко входу.
//
// Без этого лишний позиционный аргумент связывается со следующим параметром по порядку
// объявления. У role-validate им был -OutFile, и вызов вида
//   role-validate.ps1 "Roles/Роль" "Roles/Роль.xml"
// перезаписывал XML роли текстом валидационного отчёта. Остальных скриптов семьи спасала
// случайность типов ([int]MaxErrors/Limit, строка с [ValidateSet] — падают на конвертации),
// то есть любая перестановка параметров в param() открывала дыру заново.
// Снапшот-тесты этого не видят: они сверяют вывод, а не связывание аргументов.
//
// Две области. Read-only навыки: позиционным остаётся путь ко входу (Position=0), не более
// одного. Пишущие навыки: позиционных параметров нет вовсе — «главный» параметр там не
// выводится механически (у meta-edit первым объявлен -DefinitionFile, а путь к объекту вторым),
// и ошибка выбора не ловится тестами: раннер передаёт только именованные флаги. Молчаливое
// растекание там уже стреляло: `-Purpose Форма списка` у form-compile отдавало
// Purpose=[Форма] ObjectPath=[списка] БЕЗ ошибки, тогда как py-порт тот же вызов отвергал.
//
// Статическая проверка: [CmdletBinding(PositionalBinding=$false)] объявлен, число Position
// не выше нормы области; в py-порте все add_argument именованные.
// Поведенческая: лишний позиционный аргумент, указывающий на канареечный файл, роняет вызов
// и файл остаётся байт-в-байт. Валидные фикстуры не нужны — связывание параметров происходит
// до тела скрипта. Только для read-only навыков: у web-stop и db-run все параметры
// Mandatory=$false, связывание пройдёт и ТЕЛО ВЫПОЛНИТСЯ — гард остановил бы Apache и запустил
// 1С. Выход 1 при нарушении. Запуск: node tests/skills/check-positional-binding.mjs [--runtime python]
import { spawnSync } from 'node:child_process';
import { readFileSync, writeFileSync, readdirSync, existsSync, mkdtempSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { tmpdir } from 'node:os';
// fs.rmSync/fs.cpSync напрямую не зовём: на Windows они молча ничего не делают,
// когда в пути есть не-ASCII символы. Подробности и таблица сборок — в самом модуле.
import { removePathSync } from '../common/fsutil.mjs';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..', '..');
const SKILLS_DIR = join(ROOT, '.claude', 'skills');
const IS_WIN = process.platform === 'win32';

// PowerShell вне Windows не исполняется — это природа платформы, а не пробел в покрытии
// (см. debug/macmini-testing.md).
const requested = process.argv.includes('--runtime')
  ? [process.argv[process.argv.indexOf('--runtime') + 1] === 'python' ? 'python' : 'powershell']
  : ['powershell', 'python'];
const runtimes = requested.filter(rt => rt !== 'powershell' || IS_WIN);
if (requested.includes('powershell') && !IS_WIN) {
  console.log(`[powershell] пропущен: PowerShell не исполняется на ${process.platform}`);
}
if (runtimes.length === 0) {
  console.log('Нечего проверять: запрошен только powershell, а он на этой ОС не исполняется.');
  process.exit(1);
}

const PY = process.env.PYTHON || (IS_WIN ? 'python' : 'python3');

// Семья read-only навыков определяется по имени, а не списком: новый *-info/*-validate
// попадает под гард сам, без правки этого файла. Исключения перечислены явно, когда имя
// описывает операцию чтения без общего суффикса.
const isReadOnly = name => /-(info|validate)$/.test(name) || ['cfe-diff', 'db-query'].includes(name);

const skills = readdirSync(SKILLS_DIR)
  .filter(isReadOnly)
  .filter(name => existsSync(join(SKILLS_DIR, name, 'scripts', `${name}.ps1`)))
  .sort();

if (skills.length === 0) {
  console.error('Не найдено ни одного read-only навыка со скриптом — гард потерял цель.');
  process.exit(1);
}

// Пишущие навыки — всё остальное со скриптом. Список тоже по имени, чтобы новый навык
// попадал под гард сам.
const writeSkills = readdirSync(SKILLS_DIR)
  .filter(name => !isReadOnly(name))
  .filter(name => existsSync(join(SKILLS_DIR, name, 'scripts', `${name}.ps1`)))
  .sort();

if (writeSkills.length === 0) {
  console.error('Не найдено ни одного пишущего навыка со скриптом — гард потерял цель.');
  process.exit(1);
}

const violations = [];
const fail = (skill, msg) => violations.push(`${skill}: ${msg}`);

// --- 1. Статическая проверка объявлений ---

function paramBlock(text) {
  const start = text.indexOf('\nparam(\n');
  if (start < 0) return null;
  const end = text.indexOf('\n)\n', start);
  return end < 0 ? null : text.slice(start, end);
}

function checkStatic(skill, allowPositional) {
  const ps1Path = join(SKILLS_DIR, skill, 'scripts', `${skill}.ps1`);
  const ps1 = readFileSync(ps1Path, 'utf8');

  if (!/\[CmdletBinding\(\s*PositionalBinding\s*=\s*\$false\s*\)\]\s*\nparam\(/.test(ps1)) {
    fail(skill, '.ps1: нет [CmdletBinding(PositionalBinding=$false)] перед param()');
  }

  const block = paramBlock(ps1);
  if (block === null) {
    fail(skill, '.ps1: не разобран блок param()');
  } else {
    const positions = [...block.matchAll(/Position\s*=\s*(\d+)/g)].map(m => m[1]);
    if (!allowPositional && positions.length > 0) {
      fail(skill, `.ps1: у пишущего навыка не должно быть позиционных параметров (Position=${positions.join(', ')})`);
    } else if (allowPositional && positions.length > 1) {
      fail(skill, `.ps1: позиционных параметров больше одного (Position=${positions.join(', ')})`);
    } else if (allowPositional && positions.length === 1 && positions[0] !== '0') {
      fail(skill, `.ps1: единственный позиционный параметр должен быть Position=0, а не Position=${positions[0]}`);
    }
  }

  const pyPath = join(SKILLS_DIR, skill, 'scripts', `${skill}.py`);
  if (existsSync(pyPath)) {
    const py = readFileSync(pyPath, 'utf8');
    for (const m of py.matchAll(/add_argument\(\s*(['"])([^'"]+)\1/g)) {
      if (!m[2].startsWith('-')) {
        fail(skill, `.py: позиционный аргумент argparse '${m[2]}' — все параметры должны быть именованными`);
      }
    }
  }
}

for (const skill of skills) checkStatic(skill, true);
for (const skill of writeSkills) checkStatic(skill, false);

// --- 2. Поведенческая проверка (только read-only, см. шапку): лишний позиционный аргумент не трогает файл ---

const CANARY = 'канарейка: этот файл не должен быть перезаписан отчётом навыка\n';
const work = mkdtempSync(join(tmpdir(), 'posbind-'));

try {
  for (const runtime of runtimes) {
    for (const skill of skills) {
      const ext = runtime === 'python' ? '.py' : '.ps1';
      const script = join(SKILLS_DIR, skill, 'scripts', `${skill}${ext}`);
      if (!existsSync(script)) continue;

      const canary = join(work, `${skill}-${runtime}.canary`);
      writeFileSync(canary, CANARY, 'utf8');

      // Первый аргумент — заведомо несуществующий вход, второй — лишний: связывание
      // параметров происходит до тела скрипта, поэтому до чтения входа дело не доходит.
      const bogus = join(work, 'нет-такого-пути');
      const [cmd, argv] = runtime === 'python'
        ? [PY, [script, bogus, canary]]
        : ['powershell.exe', ['-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', script, bogus, canary]];

      const r = spawnSync(cmd, argv, { cwd: work, encoding: 'utf8' });

      // Не запустившийся интерпретатор даёт status: null — «код возврата ненулевой» прошло бы
      // вакуумно, и гард молча зеленел бы, ничего не проверив.
      if (r.error) {
        fail(skill, `[${runtime}] не удалось запустить ${cmd}: ${r.error.message}`);
        continue;
      }
      if (r.status === 0) {
        fail(skill, `[${runtime}] лишний позиционный аргумент принят без ошибки`);
      }
      if (readFileSync(canary, 'utf8') !== CANARY) {
        fail(skill, `[${runtime}] лишний позиционный аргумент ПЕРЕЗАПИСАЛ указанный файл`);
      }
    }
  }
} finally {
  removePathSync(work);
}

// --- Итог ---

if (violations.length) {
  console.error(`Нарушений: ${violations.length}\n`);
  for (const v of violations) console.error(`  ${v}`);
  process.exit(1);
}

console.log(`OK — read-only: ${skills.length}, пишущих: ${writeSkills.length}, рантаймы: ${runtimes.join(', ')}.`);
console.log('У read-only позиционным остаётся только путь ко входу (и лишний аргумент падает, файл цел);');
console.log('у пишущих позиционных параметров нет вовсе.');
