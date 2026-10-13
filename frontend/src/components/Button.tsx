import * as React from 'react'
import { Slot } from '@radix-ui/react-slot'
import { cva, type VariantProps } from 'class-variance-authority'
import { clsx } from 'clsx'
import { twMerge } from 'tailwind-merge'

// The shadcn/ui Button composition, adapted to the project's Studio CSS tokens.
const buttonVariants = cva('button', { variants: { variant: { primary: 'button-primary', secondary: 'button-secondary', ghost: 'button-ghost' }, size: { default: '', small: 'button-small', icon: 'button-icon' } }, defaultVariants: { variant: 'primary', size: 'default' } })
type ButtonProps = React.ButtonHTMLAttributes<HTMLButtonElement> & VariantProps<typeof buttonVariants> & { asChild?: boolean }
export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(({ className, variant, size, asChild = false, type = 'button', ...props }, ref) => {
  const Component = asChild ? Slot : 'button'
  return <Component ref={ref} className={twMerge(clsx(buttonVariants({ variant, size }), className))} type={type} {...props} />
})
Button.displayName = 'Button'
