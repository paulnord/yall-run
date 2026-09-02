# sqrt(2) continued-fraction chain

This example computes successive rational approximations to $\sqrt{2}$ using a recursion that follows directly from

$$
x^2 = 2.
$$

Since

$$
x^2 - 1 = 1,
$$

we have

$$
(x-1)(x+1)=1,
$$

and therefore

$$
x = 1 + \frac{1}{x+1}.
$$

Substituting the expression for $x$ back into itself gives the continued fraction

$$
\sqrt{2}
= 1 + \cfrac{1}{2 + \cfrac{1}{2 + \cfrac{1}{2 + \ddots}}}.
$$

Starting from $x_0=1$, the workflow applies

$$
x_{n+1}=1+\frac{1}{x_n+1}
$$

and produces the exact rational convergents

$$
1,\quad \frac32,\quad \frac75,\quad \frac{17}{12},\quad
\frac{41}{29},\ldots
$$

The example keeps every convergent as an exact Python `Fraction` until the final check.

## Why this is a useful yawl example

Unlike the parallel pi example, this calculation is deliberately serial. Each approximation depends on the previous one, so the campaign is a deep dependency chain:

```text
prepare -> seed -> step-01 -> step-02 -> ... -> step-12 -> check
```

Increasing local `-j` cannot make the mathematical chain parallel. That makes it a useful test that yawl respects dependencies even when plenty of execution capacity is available.

## Numerical character

For $\sqrt{2}$ this continued fraction is excellent. Its convergents approach the answer rapidly, alternate around the true value, and remain exact rational numbers throughout the iteration. After only twelve recursive steps the error is already about $3\times10^{-10}$.

It is not, however, a general-purpose high-performance square-root algorithm. Newton's method converges even faster and is the usual choice for numerical square roots. The continued fraction is interesting here because the mathematics naturally creates a long dependency chain.

## Run it

From this directory:

```bash
yawl-run validate
yawl-run plan
yawl-run create -j 4 | yawl-run start
cat sqrt2-work/sqrt2.txt
```

Although `-j 4` permits four local tasks at once, only one numerical step can be runnable at a time because of the dependency chain.
